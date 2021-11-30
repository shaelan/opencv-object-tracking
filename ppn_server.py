import socket
import select
import cv2
import queue
import pickle
import threading
import imagezmq
import zmq
import argparse
from imutils.video import VideoStream


def int_with_none(value):
    if value == 'None':
        return None
    else:
        return int(value)


HEADER_LENGTH = 10

IP = "127.0.0.1"
PORT = 1234

# construct the argument parser and parse the arguments
ap = argparse.ArgumentParser()
ap.add_argument("-s", "--server-ip", required=True,
                help="ip address of the server to which the client will connect")
ap.add_argument("-f", "--flip-code", required=False, default=None, type=int_with_none, choices=[0, 1, -1, None],
                help="flip code: A flag to specify how to flip the image;"
                     "0 means flipping around the x-axis;"
                     "1 means flipping around y-axis;"
                     "-1 means flipping around both axes."
                     "None means cv2.flip is not called (default).")
ih_args = ap.parse_args()

(major_ver, minor_ver, subminor_ver) = (cv2.__version__).split('.')




def sender_start(connect_to=None):
    print("connect to ImageSender")
    sender = imagezmq.ImageSender(connect_to=connect_to)
    sender.zmq_socket.setsockopt(zmq.LINGER, 0)  # prevents ZMQ hang on exit
    # NOTE: because of the way PyZMQ and imageZMQ are implemented, the
    #       timeout values specified must be integer constants, not variables.
    #       The timeout value is in milliseconds, e.g., 2000 = 2 seconds.
    sender.zmq_socket.setsockopt(zmq.RCVTIMEO, 1000)  # set a receive timeout
    sender.zmq_socket.setsockopt(zmq.SNDTIMEO, 1000)  # set a send timeout
    print("connection established to", connect_to)

    return sender


def send_client_message(client_socket, message_tuple):
    message = pickle.dumps(message_tuple)
    message_header = f"{len(message):<{HEADER_LENGTH}}".encode('utf-8')
    client_socket.sendall(message_header + message)


class Streamer(threading.Thread):
    def __init__(self, client_socket, thread_queue):
        threading.Thread.__init__(self, args=(), kwargs=None)
        self.daemon = True
        self.client_socket = client_socket
        self.my_queue = thread_queue
        self.client_name = socket.gethostname()

        # Not all these trackers appear to work with the current opencv ('4.5.4-dev')
        # self.tracker_types = ['BOOSTING', 'MIL', 'KCF', 'TLD', 'MEDIANFLOW', 'GOTURN', 'MOSSE', 'CSRT']
        self.tracker_types = ['MIL', 'KCF', 'CSRT']
        self.tracker_type = self.tracker_types[1]
        self.tracker = self.setup_tracker()
        self.tracker_ok = False
        self.flip_list = [0, 1, -1, None]
        self.vs = None
        self.sender = None

        # Caches the selection of roi_frame and roi for changing trackers without making a new selection
        self.roi_frame = None
        self.roi = None
        print("thread init")

    def sender_stop(self):
        print("Release VS")
        self.vs.stream.release()
        self.vs.stop()
        print("VS Released.")
        self.sender.close()
        del self.vs

    def run(self):
        print("thread running")
        frame_cropped_len = 0
        connect_to = "tcp://{}:5555".format(ih_args.server_ip)
        self.sender = sender_start(connect_to)
        self.vs = VideoStream(src=0).start()

        time_between_restarts = 15  # number of seconds to sleep between sender restarts
        jpeg_quality = 95  # 0 to 100, higher is better quality, 95 is cv2 default
        print("beginning outer try")
        '''
        New model for this:
         - Run and send images, with or without overlay
         - Process message receipts when they are present in the queue
         - adjust the display accordingly based on the messages
         - for a disconnect message, the server will shutdown this thread
         - it must happen only after the image is sent, so the client will have to disconnect on a timer
         - also, stoppable may no-longer be necessary if the new model works properly
         - however, since blocking can still occur, this is where the imagezmq socket options for default timeout
           need to be set
        '''

        while True:
            # print("read frame")
            # Read a new frame (this must be above queue processing since set_roi overwrites the frame data once
            frame = self.vs.read()
            if ih_args.flip_code is not None:
                frame = cv2.flip(frame, ih_args.flip_code)

            # ret_code, jpg_buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality])

            # print("frame read")
            try:
                # process a queue message
                val = self.my_queue.get_nowait()
                if val:
                    message, *args = val
                    print("got from queue", message)
                    '''
                    disconnect ('disconnect', client_timeout_in_sec)
                        responds to the client with a disconnect_ok message once the video stream has been stopped.
                        note: a timeout of 0 means the client has already disconnected, so no response is issued.
                    '''
                    if message == 'disconnect':
                        self.sender_stop()
                        self.my_queue.task_done()
                        break

                    '''
                    set_roi  ('set_roi', frame, roi)
                        with this style of message handling it will be implemented a little differently. The arguments
                        are processed by the running thread so no streaming delay should occur
                    '''
                    if message == 'set_roi':
                        self.roi_frame, self.roi = args
                        print("> frame and roi are present")
                        frame_cropped_len = len(self.roi_frame[int(self.roi[1]):int(self.roi[1] + self.roi[3]),
                                                               int(self.roi[0]):int(self.roi[0] + self.roi[2])])
                        if frame_cropped_len > 0:
                            print("> cropped frame selected", self.roi)
                            # Initialize tracker with input frame and bounding box
                            del self.tracker
                            self.tracker = self.setup_tracker()
                            self.tracker_ok = self.tracker.init(self.roi_frame, self.roi)

                    '''
                    get_frame  ('get_frame')
                        requests the raw frame data be sent via socket, for the client to use in a selectROI window
                    '''
                    if message == 'get_frame':
                        send_client_message(self.client_socket, ('raw_selection_data', frame, 1))

                    '''
                    clear_roi ('clear_roi')
                        the arguments are processed by the running thread and the tracker is disabled
                    '''
                    if message == 'clear_roi':
                        roi = None
                        frame_cropped_len = 0

                    '''
                    trackers ('trackers')
                        responds with a list of server-supported trackers and the current tracker's index in that list
                    '''
                    if message == 'trackers':
                        send_client_message(self.client_socket, ('tracker_list', self.tracker_types,
                                            self.tracker_types.index(self.tracker_type)))

                    '''
                    set_tracker ('set_tracker', tracker_array_index_from_client)
                        selects a tracker, re-initializing the tracking algorithm as needed
                    '''
                    if message == 'set_tracker':
                        self.tracker_type = self.tracker_types[args[0]]
                        if self.roi_frame is not None and self.roi is not None:
                            print("< frame and roi are present")
                            frame_cropped_len = len(self.roi_frame[int(self.roi[1]):int(self.roi[1] + self.roi[3]),
                                                                   int(self.roi[0]):int(self.roi[0] + self.roi[2])])
                            if frame_cropped_len > 0:
                                print("< cropped frame selected", self.roi)
                                # Initialize tracker with input frame and bounding box
                                del self.tracker
                                self.tracker = self.setup_tracker()
                                self.tracker_ok = self.tracker.init(self.roi_frame, self.roi)

                    '''flip ('flip', flip_index)
                        adjusts the value stored in ih_args.flip_code) for the server-side call to cv2.flip 
                        list index: 0, 1, 2, 3 corresponding to the server-side list index of [0, 1, -1, None]
                    '''
                    if message == 'set_flip':
                        print(message, args)
                        ih_args.flip_code = self.flip_list[args[0]]

                self.my_queue.task_done()
            except queue.Empty:
                pass

            if frame_cropped_len:
                ok = True
                tracker_frame = frame
                if not ok:
                    break

                # Start timer
                timer = cv2.getTickCount()

                # Update tracker
                ok, bbox = self.tracker.update(tracker_frame)

                # Calculate Frames per second (FPS)
                fps = cv2.getTickFrequency() / (cv2.getTickCount() - timer)

                # Draw bounding box
                if ok:
                    # Tracking success
                    p1 = (int(bbox[0]), int(bbox[1]))
                    p2 = (int(bbox[0] + bbox[2]), int(bbox[1] + bbox[3]))
                    cv2.rectangle(tracker_frame, p1, p2, (255, 0, 0), 2, 1)

                    # Some sample text
                    # cv2.putText(frame, "Test Text Here", (100, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 0, 255), 2)
                    frame_height, frame_width = tracker_frame.shape[:2]
                    crosshair_col = int(frame_height / 2)
                    crosshair_row = int(frame_width / 2)

                    # print("frame height = " + str(frame_height) + " frame width = " + str(frame_width))

                    crosshair_p1 = (crosshair_row - 20, crosshair_col)
                    crosshair_p2 = (crosshair_row + 20, crosshair_col)
                    crosshair_p3 = (crosshair_row, crosshair_col - 20)
                    crosshair_p4 = (crosshair_row, crosshair_col + 20)

                    #
                    target_x = int(bbox[0] + int(bbox[2] / 2))
                    target_y = int(bbox[1] + int(bbox[3] / 2))

                    # target_centroid = (int(bbox[0]+int(bbox[2]/2), int(bbox[1]+int(bbox[3]/2))))

                    # print statements for debugging
                    # print("X = " + str(int(bbox[0] + int(bbox[2] / 2))) + " Y = " + str(
                    #    int(bbox[1] + int(bbox[3] / 2))))

                    cv2.circle(tracker_frame, (target_x, target_y), 5, (255, 255, 255), 5)

                    # Draw saw crosshair
                    cv2.line(tracker_frame, crosshair_p1, crosshair_p2, (255, 255, 255), 5)
                    cv2.line(tracker_frame, crosshair_p3, crosshair_p4, (255, 255, 255), 5)

                    # Draw line from crosshair to saw
                    cv2.line(tracker_frame, (crosshair_row, crosshair_col), (target_x, target_y), (0, 255, 0), 5)

                    x_displacement = target_x - crosshair_row
                    y_displacement = crosshair_col - target_y
                else:
                    # Tracking failure
                    cv2.putText(tracker_frame, "Tracking failure detected", (20, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.75,
                                (170, 50, 50), 2)

                # Display tracker type on frame
                cv2.putText(tracker_frame, self.tracker_type + " Tracker", (20, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.75,
                            (50, 170, 50), 2);

                # Display FPS on frame
                cv2.putText(tracker_frame, "FPS : " + str(int(fps)), (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.75,
                            (50, 170, 50), 2);

                # Display x displacement
                cv2.putText(tracker_frame, "x displacement : " + str(x_displacement), (20, 60),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.75,
                            (50, 170, 50), 2);

                # Display y displacement
                cv2.putText(tracker_frame, "y displacement : " + str(y_displacement), (20, 80),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.75,
                            (50, 170, 50), 2);
                frame = tracker_frame

            try:
                self.sender.send_image(self.client_name, frame)
            except (zmq.ZMQError, zmq.ContextTerminated, zmq.Again):
                self.sender_stop()
                print('286: Closing ImageSender.')
                break

        # end while loop
        print("thread ending")

    def setup_tracker(self):
        tracker = None
        if int(minor_ver) < 3:
            tracker = cv2.Tracker_create(self.tracker_type)
        else:
            if self.tracker_type == 'BOOSTING':
                tracker = cv2.TrackerBoosting_create()
            if self.tracker_type == 'MIL':
                tracker = cv2.TrackerMIL_create()
            if self.tracker_type == 'KCF':
                tracker = cv2.TrackerKCF_create()
            if self.tracker_type == 'TLD':
                tracker = cv2.TrackerTLD_create()
            if self.tracker_type == 'MEDIANFLOW':
                tracker = cv2.TrackerMedianFlow_create()
            if self.tracker_type == 'GOTURN':
                tracker = cv2.TrackerGOTURN_create()
            if self.tracker_type == 'MOSSE':
                tracker = cv2.TrackerMOSSE_create()
            if self.tracker_type == "CSRT":
                tracker = cv2.TrackerCSRT_create()

        return tracker


def receive_message(client_socket):
    try:
        message_header = client_socket.recv(HEADER_LENGTH)

        if not len(message_header):
            return False

        message_length = int(message_header.decode('utf-8').strip())
        return {'header': message_header, 'data': client_socket.recv(message_length)}

    except:
        return False


def main():
    # Create a socket socket.AF_INET - address family, IPv4, some otehr possible are AF_INET6, AF_BLUETOOTH,
    # AF_UNIX socket.SOCK_STREAM - TCP, conection-based, socket.SOCK_DGRAM - UDP, connectionless, datagrams,
    # socket.SOCK_RAW - raw IP packets
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    # SO_ - socket option
    # SOL_ - socket option level
    # Sets REUSEADDR (as a socket option) to 1 on socket
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    # Bind, so server informs operating system that it's going to use given IP and port For a server using 0.0.0.0
    # means to listen on all available interfaces, useful to connect locally to 127.0.0.1 and remotely to LAN
    # interface IP
    server_socket.bind((IP, PORT))

    # This makes server listen to new connections
    server_socket.listen()

    # List of sockets for select.select()
    sockets_list = [server_socket]

    print(f'Listening for connections on {IP}:{PORT}...')

    while True:
        # Calls Unix select() system call or Windows select() WinSock call with three parameters:
        #   - rlist - sockets to be monitored for incoming data
        #   - wlist - sockets for data to be send to (checks if for example buffers are not full and socket is ready to
        #             send some data)
        #   - xlist - sockets to be monitored for exceptions (we want to monitor all sockets for errors, so we can use
        #             rlist)
        # Returns lists:
        #   - reading - sockets we received some data on (that way we don't have to check sockets manually)
        #   - writing - sockets ready for data to be send thru them
        #   - errors  - sockets with some exceptions
        # This is a blocking call, code execution will "wait" here and "get" notified in case any action should be taken
        read_sockets, _, exception_sockets = select.select(sockets_list, [], sockets_list)

        # Iterate over notified sockets
        for notified_socket in read_sockets:

            if notified_socket == server_socket:

                client_socket, client_address = server_socket.accept()

                # Client should send his name right away, receive it
                user = receive_message(client_socket)

                # If False - client disconnected before he sent his name
                if user is False:
                    continue

                # Add accepted socket to select.select() list
                sockets_list.append(client_socket)

                # Also save username and username header
                clients[client_socket] = user

                print('Accepted new connection from {}:{}, username: {}'.format(*client_address, user['data'].decode('utf-8')))

                t = Streamer(client_socket, queue.Queue())
                t.start()

                # save the thread reference
                threads[client_socket] = t

                for thread in threading.enumerate():
                    print('thread >', thread.name)

            # Else existing socket is sending a message
            else:
                # Receive message
                message = receive_message(notified_socket)

                # If False, client disconnected, cleanup
                if message is False:
                    print('Closed connection from: {}'.format(clients[notified_socket]['data'].decode('utf-8')))
                    t = threads[notified_socket]
                    t.my_queue.put(('disconnect', -1))
                else:
                    user = clients[notified_socket] # Get user by notified socket, so we will know who sent the message

                    '''
                    This message architecture can help to re-implement REQ/REP messaging, which seems more reliable and
                    less prone to socket errors, among other things. So I will revert portions of these scripts to November
                    19 @ 11:39AM when the necessary code was present. Then the new messaging can start to get implemented.
                    more messages are coming:
                    set_roi  ('set_roi', frame, roi)
                        with this style of message handling it will be implemented a little differently. The arguments are
                        processed by the running thread so  no streaming delay should occur
                    clear_roi ('clear_roi')
                        the arguments are processed by the running thread and the tracker is disabled
                    trackers ('trackers')
                        responds with a list of server-supported trackers. a page of buttons is shown, the client picks one, 
                        and that pick sends the set_tracker message defined below
                    set_tracker ('set_tracker', tracker_array_index_from_server)
                        selects a tracker, re-initializing the tracking algorithm as needed
                    disconnect ('disconnect', client_timeout_in_sec)
                        responds to the client with a disconnect_ok message once the video stream has been stopped.
                        note: a timeout of 0 means the client has already disconnected, so no response is issued.
                    new message format is a tuple (text, arguments)
                    '''
                    t = threads[notified_socket]
                    t.my_queue.put(pickle.loads(message['data']))
                    print('530 putting in queue: ', pickle.loads(message['data'])[0])

                if message is False or pickle.loads(message['data'])[0] == 'disconnect':
                    print(t.name, 'shutting down thread')
                    t.join()
                    if message:
                        print(t.name, 'done joining thread', message, pickle.loads(message['data'])[0])
                        send_client_message(notified_socket, ('disconnect_ok',))
                    else:
                        print("forced closure")
                    print('Closing socket')
                    sockets_list.remove(notified_socket)
                    del clients[notified_socket]
                    notified_socket.close()

        # It's not really necessary to have this, but will handle some socket exceptions just in case
        for notified_socket in exception_sockets:
            print("** disconnecting a client")
            t = threads[notified_socket]
            t.my_queue.put('disconnect', 0)
            t.join()
            print("** thread ended")

        # @TODO: add a way to cleanly exit from this server.


if __name__ == '__main__':
    # List of connected clients - socket as a key, user header and name as data
    clients = {}

    # list of client-threads - socket as a key, thread identifier as data
    threads = {}

    main()
