# !/usr/bin/env python
# coding: utf-8
import random
import cv2 as cv
import numpy as np
import tkinter as tk


BLACK_MAX_VALUE = 80
BLACK_MAX_CHROMA = 35


class update_hsv:
    def __init__(self):
        '''
        初始化一些参数
        '''
        self.image = None
        self.binary = None
        self.hsvname = None
        self.detected_colors = []  # 用于存储检测到的颜色名称
        self.detected_colors_xy = []  # 用于存储检测到的颜色的中心点
    def Image_Processing(self, hsv_range, hsv_name=None):
        '''
        形态学变换去出细小的干扰因素
        :param img: 输入初始图像
        :return: 检测的轮廓点集(坐标)
        '''
        if hsv_name == "black":
            max_channel = self.image.max(axis=2)
            min_channel = self.image.min(axis=2)
            dark = max_channel <= BLACK_MAX_VALUE
            neutral = (max_channel - min_channel) <= BLACK_MAX_CHROMA
            binary = np.where(dark & neutral, 255, 0).astype(np.uint8)
        else:
            (lowerb, upperb) = hsv_range
            # 将图像转换为HSV。
            hsv_img = cv.cvtColor(self.image, cv.COLOR_RGB2HSV)
            # 筛选出位于两个数组之间的元素。
            binary = cv.inRange(hsv_img, lowerb, upperb)
        # 获取不同形状的结构元素
        kernel = cv.getStructuringElement(cv.MORPH_RECT, (5, 5))
        # 形态学闭操作
        binary = cv.morphologyEx(binary, cv.MORPH_CLOSE, kernel)
        # 获取轮廓点集(坐标) python2和python3在此处略有不同
        # _, contours, heriachy = cv.findContours(binary, cv.RETR_EXTERNAL, cv.CHAIN_APPROX_SIMPLE) #python2
        contours, heriachy = cv.findContours(binary, cv.RETR_EXTERNAL, cv.CHAIN_APPROX_SIMPLE)  # python3
        return contours, binary

    def draw_contours(self, hsv_name, contours):
        '''
        采用多边形逼近的方法绘制轮廓
        '''
        self.hsvname=None
        valid_contours = [cnt for cnt in contours if cv.contourArea(cnt) > 800]
        if not valid_contours:
            return self.detected_colors,self.detected_colors_xy

        cnt = max(
            valid_contours,
            key=lambda contour: (contour[:, 0, 1].max(), cv.contourArea(contour)),
        )
        # 计算多边形的矩
        mm = cv.moments(cnt)
        if mm['m00'] == 0:
            return self.detected_colors,self.detected_colors_xy

        cx = mm['m10'] / mm['m00']
        cy = mm['m01'] / mm['m00']
        # 获取多边形的中心
        (x, y) = (np.int_(cx), np.int_(cy))
        # 绘制中?
        cv.circle(self.image, (x, y), 5, (255, 0, 0), -1)
        # 计算最小矩形区域
        rect = cv.minAreaRect(cnt)
        # 获取盒?顶点
        box = cv.boxPoints(rect)
        # 转成long类型
        box = box.astype(np.intp)
        # 绘制最小矩形
        cv.drawContours(self.image, [box], 0, (0, 0, 255), 2)
        cv.putText(self.image, hsv_name, (int(x - 15), int(y - 15)),
                   cv.FONT_HERSHEY_SIMPLEX, 1, (255, 0, 255), 2)
        self.hsvname=hsv_name
        self.detected_colors.append(self.hsvname)
        self.detected_colors_xy.append([x,y])

        return self.detected_colors,self.detected_colors_xy



    def get_contours(self, img, color_hsv):
        binary = None
        self.hsvname=None
        # 规范输入图像大小
        self.image = cv.resize(img, (320, 240), )
        for key, value in color_hsv.items():
            # 检测轮廓点集
            color_contours, binary = self.Image_Processing(color_hsv[key], key)
            # 绘制检测图像,并控制跟随
            self.detected_colors,self.detected_colors_xy=self.draw_contours(key, color_contours)
        colors= self.detected_colors.copy()
        xy_coordinate = self.detected_colors_xy.copy()
        self.detected_colors.clear()  
        self.detected_colors_xy.clear() 
        return self.image, binary,colors,xy_coordinate
        
