"""

This example script illustrates how to receive the data.

You can run it in any venv, so long as socket_server.py accompanies it.

Once it is running, you can startup ppn_server.py, and then run ppn_client.py as usual.

- 2021-12-05 Jeremy Broad
"""


import pickle
import argparse
import socket_server


def displacement_received(__, message):
    """
    This method is called each time the socket_server script receives a message.
    The ppn_server script is configured to send them while tracking, if this endpoint accepts the connection.

    The first argument is a reference to the socket who delivered this message but it is not used here
    message: a dictionary containing keys 'header' and 'data'. The 'data' key is pickle-encoded.
    """
    values = pickle.loads(message['data'])
    # Currently 'values' is an (x, y) tuple
    x_displacement, y_displacement = values

    print("displacement: received x=", x_displacement, "y=", y_displacement)


def main():
    """
    Sets up the socket to receive offset data on the specified port.

    In your production script it may not be necessary to have an infinite loop (while true: continue) like this has.

    Just put the line below in the appropriate place where you want your script to begin listening for the data.

    """
    socket_server.bind_and_listen('127.0.0.1', args.port, None, None, displacement_received)
    while True:
        continue


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument("-p", "--port", required=False, type=int, default=14560,
                    help="port for data offset receipt")
    args = ap.parse_args()

    main()
