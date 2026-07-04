from subprocess import Popen
import cv2
import os,socket,sys,time
import spidev as SPI
import xgoscreen.LCD_2inch as LCD_2inch
from PIL import Image,ImageDraw,ImageFont
from key import Button
from xgolib import XGO

import sys
sys.path.append("..")


# 中文开关，默认为英文 Chinese switch. The default value is English
g_ENABLE_CHINESE = False
show_body = {

    'PRESSA': ("Press A:Record", "按下A：开始记录"),
    'PRESSB': ("Press B:End Record", "按下B：结束录制"),
    'PRESSC': ("Press C:Quit", "按下C：退出程序"),
    'PRESSD': ("Press D:Execute", "按下D：执行动作"),

    'ACTION': ("ACTION", "动作"),
    'READY': ("Action Group is Ready", "动作组已经就绪"),
    'EXECUTING': ("Action Executing", "动作组执行中"),
    'DONE': ("Action Done", "动作组执行完毕"),
    'MAX': ("Maximum record of 12 sets", "此功能最多记录12组舵机动作"),

}


#define colors
btn_selected = (24,47,223)
btn_unselected = (20,30,53)
txt_selected = (255,255,255)
txt_unselected = (76,86,127)
splash_theme_color = (15,21,46)
color_black=(0,0,0)
color_white=(255,255,255)
color_red=(238,55,59)
#display init
display = LCD_2inch.LCD_2inch()
display.Init()
display.clear()
#button
button=Button()
#const
firmware_info='v1.0'
#font
font1 = ImageFont.truetype("/home/pi/model/msyh.ttc",15)
font2 = ImageFont.truetype("/home/pi/model/msyh.ttc",22)
font3 = ImageFont.truetype("/home/pi/model/msyh.ttc",30)
splash = Image.new("RGB", (display.height, display.width ),splash_theme_color)
draw = ImageDraw.Draw(splash)
display.ShowImage(splash)
button=Button()
servo=[11, 12, 13, 21, 22, 23, 31, 32, 33, 41, 42, 43, 51, 52, 53]
num = 0
isCollect = 0
n = 0
font2 = ImageFont.truetype("/home/pi/model/msyh.ttc",22)

dog = XGO(port='/dev/ttyAMA0',version="xgolite")

def lcd_draw_string(splash,x, y, text, color=(255,255,255), font_size=1, scale=1, mono_space=False, auto_wrap=True, background_color=(0,0,0)):
    splash.text((x,y),text,fill =color,font = scale) 

def lcd_rect(x,y,w,h,color,thickness):
    draw.rectangle([(x,y),(w,h)],fill=color,width=thickness)

lcd_draw_string(draw,70,20, show_body['PRESSA'][g_ENABLE_CHINESE], color=(255,255,255), scale=font2, mono_space=False)
lcd_draw_string(draw,70,80, show_body['PRESSB'][g_ENABLE_CHINESE], color=(255,255,255), scale=font2, mono_space=False)
lcd_draw_string(draw,70,140,show_body['PRESSC'][g_ENABLE_CHINESE], color=(255,255,255), scale=font2, mono_space=False)
lcd_draw_string(draw,70,200,show_body['PRESSD'][g_ENABLE_CHINESE], color=(255,255,255), scale=font2, mono_space=False)
display.ShowImage(splash)

lcd_rect(0,0,320,240,color=color_black,thickness=-1)
data = [[],[],[],[],[],[],[],[],[],[],[],[]]

try:
    while True:    
        
        if button.press_c():#这个是A键 AKEY
            lcd_rect(0,0,320,240,color=color_black,thickness=-1)
            dog.unload_allmotor()
            data[n] = dog.read_motor()
            print(data)
            lcd_draw_string(draw,110,100, show_body['ACTION'][g_ENABLE_CHINESE]+(str(n+1)), color=(255,255,255), scale=font2, mono_space=False)
            lcd_draw_string(draw,20,150,show_body['MAX'][g_ENABLE_CHINESE], color=(255,255,255), scale=font2, mono_space=False)
            display.ShowImage(splash)
            time.sleep(0.02)
            lcd_rect(0,0,320,240,color=color_black,thickness=-1)
            n = n + 1
            print(n)
            if n > 12:
                    break                
        if button.press_d():#这个是B键 BKEY
            lcd_rect(0,0,320,240,color=color_black,thickness=-1)
            dog.load_allmotor()
            lcd_draw_string(draw,40,100, show_body['READY'][g_ENABLE_CHINESE], color=(255,255,255), scale=font2, mono_space=False)
            display.ShowImage(splash)
            time.sleep(0.02)
            lcd_rect(0,0,320,240,color=color_black,thickness=-1)
        if button.press_a(): #这个是D键 DKEY
            lcd_rect(0,0,320,240,color=color_black,thickness=-1)
            lcd_draw_string(draw,66,100, show_body['EXECUTING'][g_ENABLE_CHINESE], color=(255,255,255), scale=font2, mono_space=False)
            display.ShowImage(splash)
            time.sleep(0.02)
            lcd_rect(0,0,320,240,color=color_black,thickness=-1)  
            for d in data:
                if d!=[]:
                    dog.motor(servo,d)
                    print(d)
                    time.sleep(0.8)
            print('action done!')
            lcd_draw_string(draw,100,100, show_body['DONE'][g_ENABLE_CHINESE], color=(255,255,255), scale=font2, mono_space=False)
            display.ShowImage(splash)
        if button.press_b():#这个是C键 CKEY
            dog.load_allmotor()
            dog.reset()
            break
except:
    dog.reset()

