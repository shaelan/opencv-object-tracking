import socket
import cv2
import queue
import pickle
import imagezmq
import zmq
import argparse
import threading
import socket_server
from socket_client import SocketClient
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
ap.add_argument("-c", "--client-port", required=False, type=int, default=14560,
                help="sets the port for data offset transmission")
ih_args = ap.parse_args()

threads = {}

(major_ver, minor_ver, subminor_ver) = cv2.__version__.split('.')


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


def show_error(message):
    print('ERROR: ', message)


def app_server_connect(client_socket):
    # Also save username and username header
    t = Streamer(client_socket, queue.Queue())
    t.start()

    # save the thread reference
    threads[client_socket] = t

    for thread in threading.enumerate():
        print('thread >', thread.name)


def app_server_disconnect(client_socket, arg):
    t = threads[client_socket]
    t.my_queue.put(('disconnect', arg))
    if arg == -1:
        t.join()
        print("** thread ended")


def app_message(notified_socket, message):
    t = threads[notified_socket]
    t.my_queue.put(pickle.loads(message['data']))
    print('530 putting in queue: ', pickle.loads(message['data'])[0])

    if pickle.loads(message['data'])[0] == 'disconnect':
        print(t.name, 'shutting down thread')
        t.join()
        if message:
            print(t.name, 'done joining thread', message, pickle.loads(message['data'])[0])
            socket_server.send_message(notified_socket, ('disconnect_ok',))
        else:
            print("forced closure")


class Streamer(threading.Thread):
    def __init__(self, client_socket, thread_queue):
        threading.Thread.__init__(self, args=(), kwargs=None)
        self.daemon = True
        self.client_socket = client_socket
        self.my_queue = thread_queue
        self.client_name = socket.gethostname()
        self.offset_socket = None
        self.has_socket = False

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
        self.offset_socket.stop_listening()
        self.offset_socket.client_socket.close()
        del self.vs

    def run(self):
        print("thread running")
        frame_cropped_len = 0
        connect_to = "tcp://{}:5555".format(ih_args.server_ip)
        self.sender = sender_start(connect_to)
        self.vs = VideoStream(src=0).start()

        try:
            self.offset_socket = SocketClient(ih_args.server_ip, ih_args.client_port)
            self.has_socket = self.offset_socket.connect(show_error)
        except Exception as ex:
            print(ex)
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
                    print('173', message)
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
                        # print("> frame and roi are present")
                        frame_cropped_len = len(self.roi_frame[int(self.roi[1]):int(self.roi[1] + self.roi[3]),
                                                int(self.roi[0]):int(self.roi[0] + self.roi[2])])
                        if frame_cropped_len > 0:
                            # print("> cropped frame selected", self.roi)
                            # Initialize tracker with input frame and bounding box
                            del self.tracker
                            self.tracker = self.setup_tracker()
                            self.tracker_ok = self.tracker.init(self.roi_frame, self.roi)

                    '''
                    get_frame  ('get_frame')
                        requests the raw frame data be sent via socket, for the client to use in a selectROI window
                    '''
                    if message == 'get_frame':
                        socket_server.send_message(self.client_socket, ('raw_selection_data', frame, 1))

                    '''
                    clear_roi ('clear_roi')
                        the arguments are processed by the running thread and the tracker is disabled
                    '''
                    if message == 'clear_roi':
                        self.roi = None
                        frame_cropped_len = 0
                        if self.has_socket:
                            self.offset_socket.send(pickle.dumps((0, 0)))

                    '''
                    trackers ('trackers')
                        responds with a list of server-supported trackers and the current tracker's index in that list
                    '''
                    if message == 'trackers':
                        socket_server.send_message(self.client_socket, ('tracker_list', self.tracker_types,
                                                                        self.tracker_types.index(self.tracker_type)))

                    '''
                    set_tracker ('set_tracker', tracker_array_index_from_client)
                        selects a tracker, re-initializing the tracking algorithm as needed
                        note: this is not 100% reliable.
                    '''
                    if message == 'set_tracker':
                        self.tracker_type = self.tracker_types[args[0]]
                        if self.roi_frame is not None and self.roi is not None:
                            # print("< frame and roi are present")
                            frame_cropped_len = len(self.roi_frame[int(self.roi[1]):int(self.roi[1] + self.roi[3]),
                                                    int(self.roi[0]):int(self.roi[0] + self.roi[2])])
                            if frame_cropped_len > 0:
                                # print("< cropped frame selected", self.roi)
                                # Initialize tracker with input frame and bounding box
                                del self.tracker
                                self.tracker = self.setup_tracker()
                                self.tracker_ok = self.tracker.init(self.roi_frame, self.roi)

                    '''flip ('flip', flip_index)
                        adjusts the value stored in ih_args.flip_code) for the server-side call to cv2.flip 
                        list index: 0, 1, 2, 3 corresponding to the server-side list index of [0, 1, -1, None]
                    '''
                    if message == 'set_flip':
                        # print(message, args)
                        ih_args.flip_code = self.flip_list[args[0]]

                self.my_queue.task_done()
            except queue.Empty:
                pass
            except Exception as x:
                print(256, x)

            if frame_cropped_len:
                x_displacement = 0
                y_displacement = 0

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

                    #
                    frame_height, frame_width = tracker_frame.shape[:2]
                    crosshair_col = int(frame_height / 2)
                    crosshair_row = int(frame_width / 2)

                    #
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

                    if self.has_socket:
                        self.offset_socket.send(pickle.dumps((x_displacement, y_displacement)))

                else:
                    # Tracking failure
                    cv2.putText(tracker_frame, "Tracking failure detected", (20, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.75,
                                (170, 50, 50), 2)

                    if self.has_socket:
                        self.offset_socket.send(pickle.dumps((0, 0)))

                # Display tracker type on frame
                cv2.putText(tracker_frame, self.tracker_type + " Tracker", (20, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.75,
                            (50, 170, 50), 2)

                # Display FPS on frame
                cv2.putText(tracker_frame, "FPS : " + str(int(fps)), (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.75,
                            (50, 170, 50), 2)

                # Display x displacement
                cv2.putText(tracker_frame, "x displacement : " + str(x_displacement), (20, 60),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.75,
                            (50, 170, 50), 2)

                # Display y displacement
                cv2.putText(tracker_frame, "y displacement : " + str(y_displacement), (20, 80),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.75,
                            (50, 170, 50), 2)
                frame = tracker_frame

            try:
                self.sender.send_image(self.client_name, frame)
            except (zmq.ZMQError, zmq.ContextTerminated, zmq.Again) as e:
                self.sender_stop()
                print('Closing ImageSender.', e)
                break
            except Exception as x:
                print(354, x)

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


def main():
    socket_server.bind_and_listen(ih_args.server_ip, PORT,
                                  app_server_connect, app_server_disconnect, app_message)
    while True:
        continue


if __name__ == '__main__':
    main()
