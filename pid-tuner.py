"""

This example script illustrates how to receive the data.

You can run it in any venv, so long as socket_server.py accompanies it.

Once it is running, you can startup ppn_server.py, and then run ppn_client.py as usual.

- 2021-12-05 Jeremy Broad
"""
import socket_server
import argparse
import pickle
import time
from simple_pid import PID
import threading

# dronekit imports
from pymavlink import mavutil  # needed for command message definitions
from dronekit import connect, VehicleMode

# gui imports
import PySimpleGUI as sg
import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure


class Tracker:
    def __init__(self, addr, port, d):
        self.d = d

        # setup the default tracker parameters
        for component in ('x', 'y'):
            for k in self.d:
                setattr(self, '{}_{}'.format(component, k), self.d[k])
        self.x_displacement = 0
        self.y_displacement = 0
        self.frame_shape_0 = 0
        self.frame_shape_1 = 0

        self.x_PID = PID(self.x_kp, self.x_ki, self.x_kd, setpoint=self.x_setpoint, sample_time=self.x_sample_frequency,
                         output_limits=(self.x_lower_limit, self.x_upper_limit))
        self.y_PID = PID(self.y_kp, self.y_ki, self.y_kd, setpoint=self.y_setpoint, sample_time=self.y_sample_frequency,
                         output_limits=(self.y_lower_limit, self.y_upper_limit))
        self._is_tracking = False
        self.my_socket = None
        self.addr = addr
        self.port = port
        self.PID_outputs = {'x_offset': 0, 'x_control_variable': 0, 'y_offset': 0, 'y_control_variable': 0}
        self.last_time = time.time()

    def refresh_pid_parameters(self, my_key, my_value):
        setattr(self, my_key, my_value)
        print("set {} to {}".format(my_key, my_value))
        self.x_PID.Kp = self.x_kp
        self.x_PID.Ki = self.x_ki
        self.x_PID.Kd = self.x_kd
        self.x_PID.setpoint = self.x_setpoint
        self.x_PID.sample_time = 1 / self.x_sample_frequency
        self.x_PID.output_limits = (self.x_lower_limit, self.x_upper_limit)
        self.y_PID.Kp = self.y_kp
        self.y_PID.Ki = self.y_ki
        self.y_PID.Kd = self.y_kd
        self.y_PID.setpoint = self.y_setpoint
        self.y_PID.sample_time = 1 / self.y_sample_frequency
        self.y_PID.output_limits = (self.y_lower_limit, self.y_upper_limit)

    @property
    def is_tracking(self):
        return self._is_tracking

    @is_tracking.setter
    def is_tracking(self, new_value):
        # Enable or Disable both PID controllers
        # To disable the PID so that no new values are computed, set auto mode to False:
        # No new values will be computed when pid is called
        self._is_tracking = self.x_PID.auto_mode = self.y_PID.auto_mode = new_value
        # print("Tracking set to ", new_value)

    def displacement_received(self, __, message):
        """
        This method is called each time the socket_server created by this script receives a message.
        The ppn_server script is configured to send them while tracking, if this endpoint accepts the connection.

        The first argument (__) is a reference to the socket who delivered this message but it is not used here
        message: a dictionary containing keys 'header' and 'data'. The 'data' key is pickle-encoded.
        """
        values = pickle.loads(message['data'])
        # Currently 'values' is an (B, x, y, fs1, fs0) tuple; B is a boolean to indicate the tracking status
        # fs1 is frame.shape[1] from the image, fs0 is frame.shape[0] from the image. These are width and height.
        self.is_tracking = False
        try:
            self.is_tracking, self.x_displacement, self.y_displacement, self.frame_shape_1, self.frame_shape_0 = values
        except ValueError as e:
            print(e)

        self.update_pid_controllers()
        """
        # this is the old method of doing this.
        current_time = time.time()
        dt = current_time - self.last_time

        # If object is being tracked, determine offsets and update PID control variables
        if self.is_tracking:
            # This loop needs to be called at regular intervals to update the PID controllers and transmit the velocity
            # commands without overloading comm. buffers. Assume 2 Hz (0.5 seconds per cycle for now)
            # This loop timing needs to consider the tracking FPS and if this will be run asynchronously

            # From the simple-PID documentation: The PID works best when it is updated at regular intervals. To
            # achieve this, set sample_time to the amount of time there should be between each update and then call
            # the PID every time in the program loop. A new output will only be calculated when sample_time seconds
            # has passed:

            # Update our process variables here. These are what we are trying to control, ie. bring them to 0.
            x_offset = self.x_displacement
            y_offset = self.y_displacement

            # Update our control variables generated from both PID controllers
            x_control_variable = self.x_PID(x_offset)
            y_control_variable = self.y_PID(y_offset)
            # print("Foo", dt, self.sample_time)
            if dt >= self.sample_time:
                print(dt, x_offset, y_offset, x_control_variable, y_control_variable)
                # Assemble a mavlink message giving RIGHT and DOWN velocity commands.
                # Note that down is negative and FORWARD component is ommitted
                # This function normally takes a duration. Should this be the update period? Less?
                # mavlink_msg = generate_MAVlink_RD_message(x_control_variable, y_control_variable)

                # print(current_time, x_control_variable, y_control_variable)

                # Send the message from the companion computer to the flight controller. This should be at regular
                # intervals and within our MAVLINK protocol timing requirements, ie. not too fast
                # mavlink_msg.transmit()

                # update last_time to reflect that of the most recently-issued message.
                self.last_time = time.time()

        # print("displacement: ", self.is_tracking, "received x=", self.x_displacement, "y=", self.y_displacement)
        """

    def update_pid_controllers(self):
        if self.is_tracking:
            # the offset is scaled to a value between -1 and 1.
            x_offset = (self.x_displacement / (self.frame_shape_1 * 0.5))
            x_control_variable = self.x_PID(x_offset)

            y_offset = (self.y_displacement / (self.frame_shape_0 * 0.5))
            y_control_variable = self.y_PID(y_offset)
        else:
            x_control_variable = y_control_variable = x_offset = y_offset = 0
        print("x: {}, {}, y: {}, {}".format(x_offset, x_control_variable, y_offset, y_control_variable))
        if args.drone_control:
            """
            the args are: forward (positive for 'forward'), right (positive for 'right'), down (positive for 'down')
            the offsets are x (right is positive ), and y (down is negative).
            """
            send_frd_velocity(0, -x_control_variable, y_control_variable, 1)

        self.PID_outputs['x_offset'] = x_offset
        self.PID_outputs['x_control_variable'] = x_control_variable
        self.PID_outputs['y_offset'] = y_offset
        self.PID_outputs['y_control_variable'] = y_control_variable

    def connect(self, client_socket):
        self.my_socket = client_socket

    def disconnect(self, __, arg):
        self.my_socket = None
        self.is_tracking = False
        self.update_pid_controllers()

# vehicle methods
def arm_and_takeoff(target_altitude):
    """
    Arms vehicle and fly to aTargetAltitude.
    """

    print("Basic pre-arm checks")
    # Don't try to arm until autopilot is ready
    while not vehicle.is_armable:
        print(" Waiting for vehicle to initialise...")
        time.sleep(1)

    print("Arming motors")
    # Copter should arm in GUIDED mode
    vehicle.mode = VehicleMode("GUIDED")
    vehicle.armed = True

    # Confirm vehicle armed before attempting to take off
    while not vehicle.armed:
        print(" Waiting for arming...")
        time.sleep(1)

    print("Taking off!")
    vehicle.simple_takeoff(target_altitude)  # Take off to target altitude

    # Wait until the vehicle reaches a safe height before processing the goto (otherwise the command
    #  after Vehicle.simple_takeoff will execute immediately).
    while True:
        print(" Altitude: "), vehicle.location.global_relative_frame.alt
        # Break and return from function just below target altitude.
        if vehicle.location.global_relative_frame.alt >= target_altitude * 0.95:
            print("Reached target altitude")
            break
        time.sleep(1)


def send_frd_velocity(velocity_x, velocity_y, velocity_z, duration):
    """
    Move vehicle in direction based on specified velocity vectors.
    """
    # No method "set_position_target_local_frd_encode"
    msg = vehicle.message_factory.set_position_target_local_ned_encode(
        0,  # time_boot_ms (not used)
        0, 0,  # target system, target component
        # mavutil.mavlink.MAV_FRAME_BODY_FRD,  # frame
        mavutil.mavlink.MAV_FRAME_BODY_OFFSET_NED,  # frame
        0b0000111111000111,  # type_mask (only speeds enabled)
        0, 0, 0,  # x, y, z positions (not used)
        velocity_x, velocity_y, velocity_z,  # x, y, z velocity in m/s
        0, 0, 0,  # x, y, z acceleration (not supported yet, ignored in GCS_Mavlink)
        0, 0)  # yaw, yaw_rate (not supported yet, ignored in GCS_Mavlink)

    # send command to vehicle on 1 Hz cycle
    # for x in range(0, duration):
    vehicle.send_mavlink(msg)
    #     time.sleep(duration)


def send_global_velocity(velocity_x, velocity_y, velocity_z, duration):
    """
    Move vehicle in direction based on specified velocity vectors.
    """
    msg = vehicle.message_factory.set_position_target_global_int_encode(
        0,  # time_boot_ms (not used)
        0, 0,  # target system, target component
        mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,  # frame
        0b0000111111000111,  # type_mask (only speeds enabled)
        0,  # lat_int - X Position in WGS84 frame in 1e7 * meters
        0,  # lon_int - Y Position in WGS84 frame in 1e7 * meters
        0,  # alt - Altitude in meters in AMSL altitude(not WGS84 if absolute or relative)
        # altitude above terrain if GLOBAL_TERRAIN_ALT_INT
        velocity_x,  # X velocity in NED frame in m/s
        velocity_y,  # Y velocity in NED frame in m/s
        velocity_z,  # Z velocity in NED frame in m/s
        0, 0, 0,  # afx, afy, afz acceleration (not supported yet, ignored in GCS_Mavlink)
        0, 0)  # yaw, yaw_rate (not supported yet, ignored in GCS_Mavlink)

    # send command to vehicle on 1 Hz cycle
    # for x in range(0, duration):
    vehicle.send_mavlink(msg)
    #     time.sleep(duration)


# graph helper
def draw_figure(my_canvas, figure, loc=(0, 0)):
    figure_canvas_agg = FigureCanvasTkAgg(figure, my_canvas)
    figure_canvas_agg.draw()
    figure_canvas_agg.get_tk_widget().pack(side='top', fill='both', expand=1)
    return figure_canvas_agg


# gui helpers
def make_key(my_component, s_key):
    return '-{}_{}-'.format(my_component, s_key)


def horizontal_slider(my_component, text_label, s_key, s_resolution, s_range, s_default):
    return [sg.T(text_label + ':', expand_x=True, justification='right'),
            sg.Sl(orientation='h', resolution=s_resolution, range=s_range, enable_events=True,
                  key=make_key(my_component, s_key), default_value=s_default)]


def the_gui():
    # to get the granularity of the graph, divide window_length by delta_time.
    # other than that, delta_time is used only to set the window timeout (delta_time * window_length)
    delta_time = 0.1
    window_length = 10

    plot_time = np.arange(0, window_length, delta_time)  # x axis for all plots
    plot_x_offset = [0] * int(window_length / delta_time)  # y 1
    plot_x_control_variable = [0] * int(window_length / delta_time)  # y 2
    plot_y_offset = [0] * int(window_length / delta_time)  # y 3
    plot_y_control_variable = [0] * int(window_length / delta_time)  # y 4

    row_label = ['KP Gain', 'Ki Gain', 'Kd Gain', 'Lower Limit', 'Upper Limit', 'Sample Freq', 'Setpoint']
    slider_key = ['kp', 'ki', 'kd', 'lower_limit', 'upper_limit', 'sample_frequency', 'setpoint']
    slider_range = tuple([(0, 1)] * 3) + tuple([(-1, 1)] * 2) + ((1, 100), (0, 10))
    slider_resolution = tuple([0.01] * 3) + tuple([0.05] * 2) + tuple([1] * 2)

    """
    Below are the definitions for the default PID values.
    Both PIDs are supplied with the same defaults.
    The limits are set to -1 and +1
    Sample frequency is measured in Hz as specified.
    In the code, this is converted to a time in seconds (what the PID controller is expecting for sample_time)
    setpoint is 0 - which means the controller will attempt to use movement to minimise (zero) the displacement.
    """

    default_tracker_variables = {'kp': 1, 'ki': 0.1, 'kd': 0.05,
                                 'lower_limit': -1, 'upper_limit': 1,
                                 'sample_frequency': 2, 'setpoint': 0}

    frame_label = '{} Axis Controller'
    frames = []

    for the_component in ('x', 'y'):
        frames.append(sg.Fr(frame_label.format(the_component),
                            [horizontal_slider(the_component, row_label[i], slider_key[i], slider_resolution[i],
                                               slider_range[i], default_tracker_variables[slider_key[i]])
                             for i in range(0, 7)], expand_x=True, title_location=sg.TITLE_LOCATION_TOP))

    layout = [[sg.T('PID Controller Parameters:')],
              frames,
              [sg.Canvas(size=(640, 480), key='-CANVAS-')],
              [sg.Button('Show'), sg.Exit()]]

    window = sg.Window('Object Tracking Parameters', layout, finalize=True)

    """
    import the Tracker, set it up, and then we can send updates to its values from the GUI
    """
    t = Tracker('127.0.0.1', args.port, default_tracker_variables)

    tracking_states = ["off", "on"]  # for the graph title

    canvas_elem = window['-CANVAS-']
    canvas = canvas_elem.TKCanvas

    # draw the initial plot in the window
    fig = Figure()
    ax = fig.add_subplot(111)
    ax.set_xlabel("Time [s]")
    ax.set_ylabel("Value")
    fig.suptitle('Test Plot')

    ax.grid()
    fig_agg = draw_figure(canvas, fig)

    ax.set_xlabel("Time [s]")
    ax.set_ylabel("X Setpoint Value")
    fig.suptitle('Test Plot')

    threading.Thread(target=socket_server.bind_and_listen,
                     args=(t.addr, t.port, t.connect, t.disconnect, t.displacement_received), daemon=True).start()

    # force window to draw quickly
    event, values = window.read(timeout=0)

    last_time = time.time()
    while True:  # Main GUI Event Loop

        event, values = window.read(timeout=int(window_length * delta_time))

        # event handler
        if event in (sg.WIN_CLOSED, 'Exit') or event == None:
            break

        if event == 'Show':
            for component in ('x', 'y'):
                for i in range(0, 7):
                    the_label = '{} {} = {}'.format(component.upper(), row_label[i],
                                                    values[make_key(component, slider_key[i])])
                    print(the_label)
        else:
            # did a slider value change?
            search_key = event[3:-1]
            res = [(val, key) for key, val in default_tracker_variables.items() if search_key in key]
            if res:  # yes it did. update the value in the dict.
                my_key = event[1:-1]
                prior_value = getattr(t, my_key)

                try:
                    t.refresh_pid_parameters(my_key, values[event])
                except ValueError as e:
                    print(e)
                    setattr(t, my_key, prior_value)
                    window[event].update(prior_value)

                print("Set tracker value {}: {}".format(my_key, values[event]))

        # graph renderer
        current_time = time.time()
        dt = current_time - last_time
        last_time = time.time()

        plot_time[:-1] = plot_time[1:]
        plot_time[-1] = plot_time[-1] + dt

        # y axis 1
        plot_x_offset[:-1] = plot_x_offset[1:]
        plot_x_offset[-1] = t.PID_outputs['x_offset']

        # y axis 2
        plot_x_control_variable[:-1] = plot_x_control_variable[1:]
        plot_x_control_variable[-1] = t.PID_outputs['x_control_variable']

        # y axis 3
        plot_y_offset[:-1] = plot_y_offset[1:]
        plot_y_offset[-1] = t.PID_outputs['y_offset']

        # y axis 4
        plot_y_control_variable[:-1] = plot_y_control_variable[1:]
        plot_y_control_variable[-1] = t.PID_outputs['y_control_variable']

        """
        ** DONE ** the thread updates the values in PID_outputs, which the graph can use for its updates.
        ** DONE ** if the slider values change, the gui will update the thread class pid values.
        """

        ax.cla()
        ax.set_xlabel("Time [s]")
        ax.set_ylabel("Values")
        fig.suptitle("PID Tuning Visualizer (Tracking {})".format(tracking_states[t.is_tracking]))
        ax.grid()
        ax.plot(plot_time, plot_x_offset)
        ax.plot(plot_time, plot_x_control_variable)
        ax.plot(plot_time, plot_y_offset)
        ax.plot(plot_time, plot_y_control_variable)
        ax.legend(["X offset", "X Control Variable", "Y Offset", "Y Control Variable"])
        fig_agg.draw()

    window.close()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument("-p", "--port", required=False, type=int, default=14560,
                    help="port for data offset receipt")
    ap.add_argument("-d", "--drone-control", type=bool, required=False, default=False,
                    help="enable or disable drone control (this script connects to a drone on the default port)")
    args = ap.parse_args()
    vehicle = None

    if args.drone_control:
        vehicle = connect('tcp:127.0.0.1:5762', wait_ready=True)
        vehicle.home_location = vehicle.location.global_frame
        arm_and_takeoff(20)

    the_gui()

    if args.drone_control:
        vehicle.close()

# For testing with mission planner, please run the following commands in two seperate terminals:
# dronekit-sitl copter --home= 48.509988, -123.415530,59,353
# then
# mavproxy --master tcp:127.0.0.1:5760 --sitl 127.0.0.1:5501 --out 127.0.0.1:14550
# Then start mission planner and connect to the SITL copter to visualize flight
# Then, run this script. Please make sure you use the -d argument set to True or it won't connect.
