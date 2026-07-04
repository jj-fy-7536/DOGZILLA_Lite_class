import argparse
import cv2
from xgolib import XGO
import xgoscreen.LCD_2inch as LCD_2inch
import HSV_Config_Two
from PIL import Image,ImageDraw,ImageFont
import PID
import time
import threading
import line_debug_stream
import line_tracker
import stop_marker
from key import Button
from picamera2 import Picamera2
import sys
from pathlib import Path
import station_navigation

try:
    import pyzbar.pyzbar as pyzbar
except ImportError:
    pyzbar = None

# 管家犬总控的共享画面总线(独立运行本脚本时不存在则静默跳过)
sys.path.append(str(Path(__file__).resolve().parent.parent / "14.housekeeper_minimal"))
try:
    import frame_bus
except ImportError:
    frame_bus = None


button=Button()

line_speed = 10 #巡线的速度 Speed of patrol line
line_color = 'black' #yellow  blue  green  red  black
line_color_step = 4 #黑色线 black line

#初始化pid Init pid
Px_line = 0.08 # move:0.25  trun 0.15  0.08
Ix_line = 0
Dx_line = 0.0001
X_line_Middle_error = 160 #图像X轴中心  Image X-axis center
X_line_track_PID = PID.PositionalPID(Px_line, Ix_line, Dx_line) 
last_line_x = X_line_Middle_error
line_tracker_state = line_tracker.LineTracker() #带转向锁定的巡线状态机 Line-follow state machine with corner lock

LINE_ROI_Y_START = 180 #只识别画面最底部，按近处黑线巡线 Only use the closest bottom band
DEBUG_STREAM_PORT = 8080
DEFAULT_LINE_RESULT = Path("/home/pi/xgoPictures/housekeeper/line_result.json")


g_dog = XGO(port='/dev/ttyAMA0',version="xgolite")
g_dog.translation(['z'],[75]) #最低 upplow
time.sleep(0.2)
g_dog.attitude(['p'],[15])
time.sleep(0.2)
g_dog.pace('slow') 
time.sleep(.2)



#要识别的颜色阈值 Color threshold to be recognized
color_hsv  = {"red"   : ((0, 70, 72), (7, 255, 255)),
              "green" : ((54, 109, 78), (77, 255, 255)),
              "blue"  : ((92, 100, 62), (121, 255, 255)),
              "yellow": ((26, 100, 91), (32, 255, 255)),
              "black" : ((0, 0, 0), (180, 255, 80))}

#屏幕清除 screen blanker
mydisplay = LCD_2inch.LCD_2inch()
mydisplay.clear()
splash = Image.new("RGB", (mydisplay.height, mydisplay.width ),"black")
mydisplay.ShowImage(splash)


picam2 = Picamera2()
picam2.configure(
    picam2.create_preview_configuration(main={"format": "RGB888", "size": (320, 240)})
)
picam2.start()

update_hsv = HSV_Config_Two.update_hsv()
debug_frame_store = line_debug_stream.FrameStore()
debug_server = line_debug_stream.ThreadingHTTPServer(
    ("0.0.0.0", DEBUG_STREAM_PORT),
    line_debug_stream.make_handler(debug_frame_store, "/stream.mjpg", line_color),
)
debug_server_thread = threading.Thread(target=debug_server.serve_forever, daemon=True)
debug_server_thread.start()
print(f"LINE_DEBUG_STREAM:http://172.20.10.4:{DEBUG_STREAM_PORT}/")


def change_line_color():
    global line_color,line_color_step
    line_color_step = (line_color_step+1)%5
    if line_color_step == 0:
        line_color = 'red'
    elif line_color_step == 1:
        line_color = 'green'
    elif line_color_step == 2:
        line_color = 'blue'
    elif line_color_step == 3:
        line_color = 'yellow'
    elif line_color_step == 4:
        line_color = 'black'

def limit_fun(input,min,max):
    if input < min:
        input = min
    elif input > max:
        input = max
    return input

def Find_color(colors_dict,num_len):
    if num_len == 0:
        return -1 #没找到 no find
    for i in range(num_len):
        if colors_dict[i] == line_color:
            return i #找到了 find!
    return -1 #没找到 no find

def update_debug_stream(frame, status):
    encoded = line_debug_stream.encode_rgb_frame_to_jpeg(frame, jpeg_quality=80)
    if encoded:
        debug_frame_store.update(encoded, status)
        if frame_bus is not None:
            frame_bus.publish_jpeg(encoded, "line")

def decode_qr_codes(frame_rgb):
    if pyzbar is None:
        return []
    gray = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2GRAY)
    return [barcode.data.decode("utf-8") for barcode in pyzbar.decode(gray)]


def TEST(args):
    global color_hsv,last_line_x
    frame_count = 0
    warned_missing_qr = False
    line_started_at = time.time()
    stop_block_detector = stop_marker.StopMarkerDetector(
        required_frames=args.stop_block_required_frames,
        min_area_ratio=args.stop_block_area_ratio,
        min_width_ratio=args.stop_block_width_ratio,
    )
    while True:
        frame_count += 1
        frame = picam2.capture_array()
        if args.target_station and pyzbar is None and not warned_missing_qr:
            print("QR decode unavailable; continuing line follow without station stop.", flush=True)
            warned_missing_qr = True
        if (
            args.target_station
            and pyzbar is not None
            and frame_count % max(1, args.qr_decode_every_frames) == 0
        ):
            decision = station_navigation.station_decision(
                decode_qr_codes(frame),
                args.target_station,
            )
            if decision is not None:
                print(decision.log_line, flush=True)
                if decision.reached:
                    g_dog.stop()
                    station_navigation.write_line_result(
                        args.line_result,
                        success=True,
                        target_station=args.target_station,
                        reached_station=decision.station,
                        mode=args.line_mode,
                    )
                    return True
        #frame = cv2.flip(frame, 1)
        roi_frame = frame[LINE_ROI_Y_START:240, :]
        active_hsv = {line_color: color_hsv[line_color]}
        frame, binary,hsvname,xylist=update_hsv.get_contours(roi_frame,active_hsv)
        if (
            args.stop_on_black_block
            and time.time() - line_started_at >= args.stop_block_ignore_seconds
            and stop_block_detector.update(binary)
        ):
            status = "STOP_BLOCK_REACHED"
            print(status, flush=True)
            g_dog.stop()
            cv2.putText(frame, status, (30,70), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255,255,255), 2)
            update_debug_stream(frame, status)
            station_navigation.write_line_result(
                args.line_result,
                success=True,
                target_station=args.target_station or "stop_block",
                reached_station="stop_block",
                mode=args.line_mode,
            )
            return True
        decision = line_tracker_state.decide(binary, last_line_x)

        if line_color == 'blue':
            cv2.putText(frame, line_color, (40,40), cv2.FONT_HERSHEY_SIMPLEX, 1, (0,0,255), 2)
        elif line_color == 'green':
            cv2.putText(frame, line_color, (40,40), cv2.FONT_HERSHEY_SIMPLEX, 1, (0,255,0), 2)
        elif line_color == 'red':
            cv2.putText(frame, line_color, (40,40), cv2.FONT_HERSHEY_SIMPLEX, 1, (255,0,0), 2)
        elif line_color == 'yellow':
            cv2.putText(frame, line_color, (40,40), cv2.FONT_HERSHEY_SIMPLEX, 1, (255,255,0), 2)
        elif line_color == 'black':
            cv2.putText(frame, line_color, (40,40), cv2.FONT_HERSHEY_SIMPLEX, 1, (255,255,255), 2)
        else:
            cv2.putText(frame, line_color, (40,40), cv2.FONT_HERSHEY_SIMPLEX, 1, (255,0,255), 2)
        cv2.line(frame, (0, 0), (320, 0), (255, 255, 0), 2)

        #frame
        if(cv2.waitKey(1)==ord('k') or  button.press_d() ):#按键按下 key put down
            change_line_color()


        if decision.found:
            # print(line_color,xylist[index]) 

            color_x = decision.x
            color_y = decision.y
            last_line_x = color_x
            status = f"{decision.mode}: x={color_x}, y={color_y}"
            print(f"{decision.mode}:{color_x}")
            cv2.line(frame, (color_x, 0), (color_x, 240), (255, 0, 0), 1)
            cv2.circle(frame, (color_x, color_y), 6, (255, 0, 0), 2)

            if decision.turn_override is not None:
                #转向锁定期：跳过PID，持续用固定值转向 Corner lock: skip PID, keep a fixed turn
                x_line_real_value = int(decision.turn_override)
            else:
                #### X的方向(控制左右) Direction of X (control left and right)
                X_line_track_PID.SystemOutput = color_x  #X 
                X_line_track_PID.SetStepSignal(X_line_Middle_error)
                X_line_track_PID.SetInertiaTime(0.01, 0.1)               
                x_line_real_value = int(X_line_track_PID.SystemOutput)

                ## x_line_real_value = limit_fun(x_line_real_value ,-18,18)
                ## g_dog.move('y',x_line_real_value)

                x_line_real_value = int(x_line_real_value * decision.turn_multiplier)

            x_line_real_value = limit_fun(
                x_line_real_value,
                -decision.turn_limit,
                decision.turn_limit,
            )
            g_dog.turn(x_line_real_value)

            g_dog.move('x',decision.speed)
            
        
        else:
            status = f"{line_color}: not found"
            g_dog.stop() 

        cv2.putText(frame, status, (40,70), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255,255,255), 2)
        update_debug_stream(frame, status)

        # 实时传回图像数据进行显示 Real-time image data transmission for display
        cv2.imshow("color_image", cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))


        #显示在小车的lcd屏幕上  Display on the LCD screen of the car
        imgok = Image.fromarray(frame)
        mydisplay.ShowImage(imgok)

        if button.press_b():
            g_dog.stop()
            return False

def build_parser():
    parser = argparse.ArgumentParser(description="Follow colored guide line, optionally stopping at a stop block.")
    parser.add_argument("--target-station", default="")
    parser.add_argument("--line-result", type=Path, default=DEFAULT_LINE_RESULT)
    parser.add_argument("--line-mode", choices=("outbound", "return"), default="outbound")
    parser.add_argument("--qr-decode-every-frames", type=int, default=3)
    parser.add_argument("--stop-on-black-block", action="store_true")
    parser.add_argument("--stop-block-ignore-seconds", type=float, default=2.0)
    parser.add_argument("--stop-block-required-frames", type=int, default=5)
    parser.add_argument("--stop-block-area-ratio", type=float, default=stop_marker.DEFAULT_MIN_AREA_RATIO)
    parser.add_argument("--stop-block-width-ratio", type=float, default=stop_marker.DEFAULT_MIN_WIDTH_RATIO)
    return parser


try:
    TEST(build_parser().parse_args())
finally:
    picam2.stop()
    picam2.close()
    debug_server.shutdown()
    debug_server.server_close()
    g_dog.stop()
