# import the necessary packages
import os
import cv2
import sys
import kivy
import pickle
import argparse
import imagezmq
import socket_client

from functools import partial
from kivy.lang import Builder
from kivy.app import App
from kivy.graphics.texture import Texture
from kivy.uix.button import Button
from kivy.uix.screenmanager import ScreenManager, Screen
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.properties import ObjectProperty

ap = argparse.ArgumentParser()
ap.add_argument("-e", "--enable-flip-codes", required=False, default=0,
                help="enable flip code buttons in UI;"
                     "0 means disabled"
                     "1 means enabled")
ap.add_argument("-f", "--flip-code", required=False, default=0,
                help="flip code: A flag to specify how to flip the image;"
                     "0 means flipping around the x-axis;"
                     "1 means flipping around y-axis;"
                     "-1 means flipping around both axes."
                     "None means cv2.flip is not called.")
ap.add_argument("-s", "--server-flip-code", required=False, default=None,
                help="server flip code: A flag to specify how the server will flip the image;"
                     "0 means flipping around the x-axis;"
                     "1 means flipping around y-axis;"
                     "-1 means flipping around both axes;"
                     "None means cv2.flip is not called.")

ih_args = ap.parse_args()

flip_list = [0, 1, -1, None]

kivy.require("1.10.1")

# initialize the frame dictionary
frameDict = {}

# For REP/REQ:
imageHub = imagezmq.ImageHub()
IH_PORT = 5556

tracker_index = None
tracker_list = None

root_widget = """
#:kivy 1.10.0
MyScreenManager:
    id: screen_manager

    connect_page: connect_page
    info_page: info_page
    cam_page: cam_page
    tracker_page: tracker_page

    ConnectPage:
        id: connect_page
        name: "connect_page"
        manager: screen_manager

    InfoPage:
        id: info_page
        name: "info_page"
        manager: screen_manager

    CamPage:
        id: cam_page
        name: "cam_page"
        manager: screen_manager

    TrackerPage:
        id: tracker_page
        name: "tracker_page"
        manager: screen_manager


<MyResponsiveGridLayout@GridLayout>:
    cols: 1 if root.width < 200 else 2 if root.width < 400 else 4

<MyTextInput@TextInput>:
    multiline: False
    size_hint_y: None
    height: self.minimum_height
    text_size: self.size
    padding: [1,0] # somehow this makes the box as tall as the labels 
    valign: 'middle'

<MyLabel@Label>:
    height: self.texture_size[1]
    size_hint: None, None
    valign: 'middle'
    halign: 'right'

<MyButton@Button>:
    size_hint: 0.05, None
    size: self.texture_size

<MyGridLayout@GridLayout>: # for Connect Page Grids
    size_hint_y: None # sets the height properly
    height: self.minimum_height

<ConnectPage>:
    id: '_connect_page_'
    name: '_connect_page_'

    AnchorLayout: 
        anchor_x: 'center'
        anchor_y: 'center'
        height: responsive_grid.height

        MyGridLayout:
            id: responsive_grid
            cols: 1 if root.width < 400 else 3
            spacing: 5, 5
            padding: 5, 5
            MyGridLayout:
                cols: 2
                MyLabel:
                    id: label_ip
                    text: "IP Address:"
                MyTextInput:
                    id: ip

            MyGridLayout:
                cols: 2
                MyLabel:
                    id: label_port
                    text: "Port:"
                MyTextInput:
                    id: port

            MyGridLayout:
                size_hint_x: 1 if root.width < 400 else None
                width: 1 if root.width < 400 else self.minimum_width
                cols: 2
                Label:
                    text: ''
                MyButton:
                    padding: 10, 0
                    size_hint_x: None
                    size_x: 56
                    id: connect_button
                    text: "Connect"
                    on_release: root.connect_button()

<InfoPage>:
    id: '_info_page_'
    name: '_info_page_'
    GridLayout:
        cols: 1
        Label:
            id: message
            text: "Message Text!"
            halign: "center"
            valign: "middle"
            font_size: '30dp'
            text_size: self.size # enable text wrapping

<CamPage>:
    GridLayout:
        rows: 2
        spacing: 5, 5
        padding: 5, 5
        FloatLayout:
            Image:
                size: min(root.size), min(root.size)
                pos_hint: {'center_x': .5, 'center_y': .5}
                id: frame_data

        AnchorLayout: 
            anchor_x: 'center'
            anchor_y: 'bottom'
            size_hint_y: None
            height: responsive_grid.height

            MyResponsiveGridLayout:
                id: responsive_grid
                height: self.minimum_height

                MyButton:
                    id: tracker_button
                    text: "Choose Tracker"
                    on_release: root.tracker_button()
                MyButton:
                    id: select_button
                    text: "Select ROI"
                    on_release: root.select_button()
                MyButton:
                    id: clear_button
                    text: "Clear ROI"
                    on_release: root.clear_button()
                MyButton:
                    id: disconnect_button
                    text: "Disconnect"
                    on_release: root.disconnect_button()

<TrackerPage>:
    id: '_tracker_page_'
    name: '_tracker_page_'
    grid: grid
    BoxLayout:
        spacing: 5, 5
        orientation: "vertical"
        Label:
            text: "Select a tracker by clicking its button:"
            font_size: 20
            size: self.texture_size
            size_hint_y: None
        GridLayout:
            id: grid
            cols: 2
            spacing: 5, 5
            padding: 5, 5
"""


if ih_args.enable_flip_codes:
    root_widget = """
    #:kivy 1.10.0
    MyScreenManager:
        id: screen_manager

        connect_page: connect_page
        info_page: info_page
        cam_page: cam_page
        tracker_page: tracker_page

        ConnectPage:
            id: connect_page
            name: "connect_page"
            manager: screen_manager

        InfoPage:
            id: info_page
            name: "info_page"
            manager: screen_manager

        CamPage:
            id: cam_page
            name: "cam_page"
            manager: screen_manager

        TrackerPage:
            id: tracker_page
            name: "tracker_page"
            manager: screen_manager


    <MyResponsiveGridLayout@GridLayout>:
        cols: 1 if root.width < 200 else 2 if root.width < 400 else 3

    <MyTextInput@TextInput>:
        multiline: False
        size_hint_y: None
        height: self.minimum_height
        text_size: self.size
        padding: [1,0] # somehow this makes the box as tall as the labels 
        valign: 'middle'

    <MyLabel@Label>:
        height: self.texture_size[1]
        size_hint: None, None
        valign: 'middle'
        halign: 'right'

    <MyButton@Button>:
        size_hint: 0.05, None
        size: self.texture_size

    <MyGridLayout@GridLayout>: # for Connect Page Grids
        size_hint_y: None # sets the height properly
        height: self.minimum_height

    <ConnectPage>:
        id: '_connect_page_'
        name: '_connect_page_'

        AnchorLayout: 
            anchor_x: 'center'
            anchor_y: 'center'
            height: responsive_grid.height

            MyGridLayout:
                id: responsive_grid
                cols: 1 if root.width < 400 else 3
                spacing: 5, 5
                padding: 5, 5
                MyGridLayout:
                    cols: 2
                    MyLabel:
                        id: label_ip
                        text: "IP Address:"
                    MyTextInput:
                        id: ip

                MyGridLayout:
                    cols: 2
                    MyLabel:
                        id: label_port
                        text: "Port:"
                    MyTextInput:
                        id: port

                MyGridLayout:
                    size_hint_x: 1 if root.width < 400 else None
                    width: 1 if root.width < 400 else self.minimum_width
                    cols: 2
                    Label:
                        text: ''
                    MyButton:
                        padding: 10, 0
                        size_hint_x: None
                        size_x: 56
                        id: connect_button
                        text: "Connect"
                        on_release: root.connect_button()

    <InfoPage>:
        id: '_info_page_'
        name: '_info_page_'
        GridLayout:
            cols: 1
            Label:
                id: message
                text: "Message Text!"
                halign: "center"
                valign: "middle"
                font_size: '30dp'
                text_size: self.size # enable text wrapping

    <CamPage>:
        GridLayout:
            rows: 2
            spacing: 5, 5
            padding: 5, 5
            FloatLayout:
                Image:
                    size: min(root.size), min(root.size)
                    pos_hint: {'center_x': .5, 'center_y': .5}
                    id: frame_data

            AnchorLayout: 
                anchor_x: 'center'
                anchor_y: 'bottom'
                size_hint_y: None
                height: responsive_grid.height

                MyResponsiveGridLayout:
                    id: responsive_grid
                    height: self.minimum_height

                    MyButton:
                        id: tracker_button
                        text: "Choose Tracker"
                        on_release: root.tracker_button()
                    MyButton:
                        id: select_button
                        text: "Select ROI"
                        on_release: root.select_button()
                    MyButton:
                        id: clear_button
                        text: "Clear ROI"
                        on_release: root.clear_button()
                    MyButton:
                        id: client_flip
                        text: "Client Flip: "
                        on_release: root.client_flip_button()
                    MyButton:
                        id: server_flip
                        text: "Server Flip: 1"
                        on_release: root.server_flip_button()
                    MyButton:
                        id: disconnect_button
                        text: "Disconnect"
                        on_release: root.disconnect_button()

    <TrackerPage>:
        id: '_tracker_page_'
        name: '_tracker_page_'
        grid: grid
        BoxLayout:
            spacing: 5, 5
            orientation: "vertical"
            Label:
                text: "Select a tracker by clicking its button:"
                font_size: 20
                size: self.texture_size
                size_hint_y: None
            GridLayout:
                id: grid
                cols: 2
                spacing: 5, 5
                padding: 5, 5
    """


class ConnectPage(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        print("build connect page")
        Clock.schedule_once(self.callback, 0.01)

    def callback(self, dt):
        # ensures label text is right-justified
        self.ids.label_ip.text_size = self.ids.label_ip.size
        self.ids.label_port.text_size = self.ids.label_port.size

        # Read settings from text file, or use empty strings
        if os.path.isfile("prev_details.txt"):
            with open("prev_details.txt", "r") as f:
                d = f.read().split(",")
                prev_ip = d[0]
                prev_port = d[1]
        else:
            prev_ip = ''
            prev_port = ''

        self.ids.port.text = prev_port
        self.ids.ip.text = prev_ip

    def connect_button(self):
        print("Pushed connect button")
        port = self.ids.port.text
        ip = self.ids.ip.text
        if port and ip:
            with open("prev_details.txt", "w") as f:
                f.write(f"{ip},{port}")
            info = f"Connecting to {ip}:{port}"
            self.parent.info_page.update_info(info)
            self.manager.current = 'info_page'
            Clock.schedule_once(self.connect, 1.0)
        else:
            self.ids.connect_button.text = "Please enter IP Address and Port before clicking Connect"

    # Connects to the server
    # (second parameter is the time after which this function had been called,
    #  we don't care about it, but kivy sends it, so we have to receive it)
    def connect(self, _):
        # Get information for sockets client
        port = int(self.ids.port.text)
        ip = self.ids.ip.text

        print("Prepare IH...")
        # initialize the ImageHub object
        imageHub.connect("tcp://{}:{}".format(ip, IH_PORT))
        print("IH connect")

        if not socket_client.connect(ip, port, show_error):
            return

        # specify initial server image-flip
        socket_client.send(pickle.dumps(('set_flip', flip_list.index(ih_args.server_flip_code))))

        self.manager.current = 'cam_page'


class InfoPage(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        print("build info page")

    # Called with a message, to update message text in widget
    def update_info(self, message):
        self.ids.message.text = message


class CamPage(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        print("build cam page")

    def on_pre_enter(self, *args):
        # must not happen on init, but can happen on start
        if ih_args.enable_flip_codes:
            self.ids.client_flip.text = 'Client Flip: ' + str(ih_args.flip_code)
            self.ids.server_flip.text = 'Server Flip: ' + str(ih_args.server_flip_code)

        if not socket_client.listening:
            print("socket startup")
            socket_client.start_listening(self.incoming_message, show_error)
            Clock.schedule_once(self.receive_frame)

    def receive_frame(self, _):
        # receive RPi name and frame from the RPi and acknowledge the receipt
        (rpiName, frame) = imageHub.recv_image()
        imageHub.send_reply(b'OK')
        if ih_args.flip_code is not None:
            frame = cv2.flip(frame, ih_args.flip_code)

        frameDict[rpiName] = frame

        buf = frame.tobytes()
        image_texture = Texture.create(size=(frame.shape[1], frame.shape[0]), colorfmt='bgr')
        image_texture.blit_buffer(buf, colorfmt='bgr', bufferfmt='ubyte')

        # display image from the texture
        self.ids.frame_data.texture = image_texture

        # tick for next frame
        Clock.schedule_once(self.receive_frame, timeout=0.01)

    # Called from sockets client on new message receipt
    def incoming_message(self, message):
        args = pickle.loads(message)
        if args[0] == 'disconnect_ok':
            print(args[0])
            socket_client.listening = False
            App.get_running_app().stop()

        if args[0] == 'tracker_list':
            global tracker_list, tracker_index
            tracker_list, tracker_index = args[1], args[2]
            self.manager.current = 'tracker_page'

        if args[0] == 'raw_selection_data':
            r = cv2.selectROI('select', args[1], False, False)
            cv2.destroyWindow("select")
            socket_client.send(pickle.dumps(('set_roi', args[1], r)))

    def tracker_button(self):
        print("requesting tracker list...")
        socket_client.send(pickle.dumps(('trackers',)))

    def select_button(self):
        print("requesting selection")
        socket_client.send(pickle.dumps(('get_frame',)))

    def client_flip_button(self):
        flip_index = (flip_list.index(ih_args.flip_code) + 1) % 4
        print(flip_index)
        ih_args.flip_code = flip_list[flip_index]
        self.ids.client_flip.text = 'Client Flip: ' + str(ih_args.flip_code)

    def server_flip_button(self):
        server_flip_index = (flip_list.index(ih_args.server_flip_code) + 1) % 4
        print(server_flip_index)
        ih_args.server_flip_code = flip_list[server_flip_index]
        socket_client.send(pickle.dumps(('set_flip', server_flip_index)))
        self.ids.server_flip.text = 'Server Flip: ' + str(ih_args.server_flip_code)

    def clear_button(selfself):
        print("clear button")
        socket_client.send(pickle.dumps(('clear_roi',)))

    def disconnect_button(self):
        print('requesting disconnect')
        Clock.unschedule(self.receive_frame)  # prevents race condition
        socket_client.send(pickle.dumps(('disconnect',)))


class TrackerPage(Screen):
    def __init__(self, **kwargs):
        super(TrackerPage, self).__init__(**kwargs)

    def on_pre_enter(self, *args):
        Clock.schedule_once(self.update)

    def on_leave(self, *args):
        self.remove_buttons()

    def update(self, dt):
        global tracker_list, tracker_index
        for index, val in enumerate(tracker_list):
            if index == tracker_index:
                text = '[ ' + tracker_list[index] + ' ]'
            else:
                text = tracker_list[index]
            val = self.ids.grid.add_widget(Button(text=text, on_press=partial(self.button_clicked, number=index)))

    def remove_buttons(self, *args):
        for child in [child for child in self.grid.children]:
            self.grid.remove_widget(child)

    def button_clicked(self, caller, number):
        # Send the tracker index in a message to server
        # return to the CamPage screen
        socket_client.send(pickle.dumps(('set_tracker', number)))
        self.manager.current = 'cam_page'


class MyScreenManager(ScreenManager):
    connect_page = ObjectProperty(None)
    info_page = ObjectProperty(None)
    cam_page = ObjectProperty(None)


class MyScreenManagerApp(App):
    def build(self):
        self.title = 'DA-RD-2'
        Window.bind(on_request_close=self.on_request_close)
        Window.bind(on_resize=self.check_resize)
        return Builder.load_string(root_widget)

    def check_resize(self, window, width, height):
        win_ref = window.children[0]
        if win_ref.current == 'connect_page':
            if width >= 400:
                win_ref.ids.connect_page.ids.connect_button.width = 76

    def on_request_close(self, _):
        print("initiating disconnect...")

        try:
            # Connect to a given ip and port
            socket_client.send(pickle.dumps(('disconnect',)))
        except Exception as e:
            print("No socket; app can close immediately")
            App.get_running_app().stop()
            return False

        return True


# Error callback function, used by sockets client
# Updates info page with an error message, shows message and schedules exit in 10 seconds
# time.sleep() won't work here - will block Kivy and page with error message won't show up
def show_error(message):
    # using Window.get_parent_window()
    print(message)
    win_ref = Window.get_parent_window().children[0]
    win_ref.ids.info_page.update_info(message)
    win_ref.current = 'info_page'
    Clock.schedule_once(sys.exit, 10)


if __name__ == "__main__":
    MyScreenManagerApp().run()
