# https://pythonprogramming.net/screen-manager-pages-screens-kivy-application-python-tutorial/

import socket
from threading import Thread

# some globals
client_socket = None
socket_thread = None
HEADER_LENGTH = 10
listening = False


# Connects to the server
def connect(ip, port, error_callback):
    global client_socket
    client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    try:
        # Connect to a given ip and port
        client_socket.connect((ip, port))
    except Exception as e:
        # Connection error
        error_callback('Connection error: {}'.format(str(e)))
        return False

    # Prepare username and header and send them We need to encode username to bytes, then count number of bytes and
    # prepare header of fixed size, that we encode to bytes as well
    username = "Connection".encode('utf-8')
    username_header = f"{len(username):<{HEADER_LENGTH}}".encode('utf-8')
    client_socket.send(username_header + username)
    return True


# Sends a message to the server
def send(msg):
    global client_socket
    # Encode message to bytes, prepare header and convert to bytes, like for username above, then send
    message = msg
    message_header = f"{len(message):<{HEADER_LENGTH}}".encode('utf-8')
    client_socket.send(message_header + message)


# Starts listening function in a thread
# incoming_message_callback - callback to be called when new message arrives
# error_callback - callback to be called on error
def start_listening(incoming_message_callback, error_callback):
    global listening, socket_thread
    if not listening:
        listening = True
        socket_thread = Thread(target=listen, args=(incoming_message_callback, error_callback), daemon=True).start()

def stop_listening():
    if socket_thread:
        print("stopping socket thread")
        socket_thread.join()
        print("socket thread stopped")

# Listens for incoming messages
def listen(incoming_message_callback, error_callback):
    global listening
    while listening:
        try:
            # Now we want to loop over received messages (there might be more than one) and print them
            while listening:
                message_header = client_socket.recv(HEADER_LENGTH)

                # If we received no data, server gracefully closed a connection, for example using socket.close() or
                # socket.shutdown(socket.SHUT_RDWR)
                if not len(message_header):
                    error_callback('Connection closed by the server')

                # Convert header to int value
                message_length = int(message_header.decode('utf-8').strip())
                message = b''
                while len(message) < message_length:
                    remains = message_length - len(message)
                    bufsize = 4096 if remains > 4096 else remains
                    buf = client_socket.recv(bufsize)
                    if not buf:
                        client_socket.close()
                        listening = False
                        break
                    message += buf

                # Print message to client
                incoming_message_callback(message)
            print("socket done listening (inner)")
        except Exception as e:
            # Any other exception - something happened, exit
            error_callback('Reading error: {}'.format(str(e)))
            print("Break after error")
            break
    print("socket done listening (outer)")
    stop_listening()
