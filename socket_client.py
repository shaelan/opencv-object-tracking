# original code from https://pythonprogramming.net/pickle-objects-sockets-tutorial-python-3/
import socket
from threading import Thread

# some globals
HEADER_LENGTH = 10


class SocketClient:
    def __init__(self, ip="127.0.0.1", port=1234):
        self.ip = ip
        self.port = port
        self.listening = False
        self.socket_thread = None
        self.client_socket = None

    # Connects to the server
    def connect(self, error_callback):
        self.client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        # don't block on socket.recv.
        self.client_socket.settimeout(0.5)

        try:
            # Connect to a given ip and port
            self.client_socket.connect((self.ip, self.port))
        except Exception as e:
            # Connection error
            error_callback('Connection error: {}'.format(str(e)))
            return False

        return True

    # Sends a message to the server
    def send(self, msg):
        # Encode message to bytes, prepare header and convert to bytes, like for username above, then send
        message = msg
        message_header = f"{len(message):<{HEADER_LENGTH}}".encode('utf-8')
        self.client_socket.sendall(message_header + message)

    def is_listening(self):
        return self.listening == True

    # Starts listening function in a thread
    # incoming_message_callback - callback to be called when new message arrives
    # error_callback - callback to be called on error
    def start_listening(self, incoming_message_callback, error_callback):
        if not self.listening:
            self.socket_thread = Thread(target=self._listen,
                                        args=(incoming_message_callback, error_callback),
                                        daemon=True)
            self.listening = True
            self.socket_thread.start()

    def stop_listening(self):
        self.listening = False
        print(__name__, "waiting to stop")
        if self.socket_thread:
            self.socket_thread.join()
        print(__name__, "stopped")

    # Listen for incoming messages
    def _listen(self, incoming_message_callback, error_callback):
        while self.listening:
            try:
                while self.listening:
                    try:
                        message_header = self.client_socket.recv(HEADER_LENGTH)

                        # If we received no data, server gracefully closed a connection, for example using
                        # socket.close() or socket.shutdown(socket.SHUT_RDWR)
                        if not len(message_header):
                            error_callback('Connection closed by the server')

                        # Convert header to int value
                        message_length = int(message_header.decode('utf-8').strip())
                        message = b''
                        while len(message) < message_length:
                            remains = message_length - len(message)
                            bufsize = 4096 if remains > 4096 else remains
                            buf = self.client_socket.recv(bufsize)
                            if not buf:
                                self.client_socket.close()
                                self.listening = False
                                break
                            message += buf

                        # Print message to client
                        incoming_message_callback(message)
                    except socket.timeout:
                        continue
                print(__name__, "socket done listening (inner)", self.listening)
            except Exception as e:
                # Any other exception - something happened, exit
                error_callback('Reading error: {}'.format(str(e)))
                print(__name__, "Break after error")
                self.listening = False
                break
        print(__name__, "socket done listening (outer)", self.listening)
