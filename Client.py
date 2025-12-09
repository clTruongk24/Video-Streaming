from tkinter import *
import tkinter.messagebox as tkMessageBox
from PIL import Image, ImageTk
import socket, threading, sys, traceback, os, time

from RtpPacket import RtpPacket
from collections import deque

CACHE_FILE_NAME = "cache-"
CACHE_FILE_EXT = ".jpg"

class Client:
	INIT = 0
	READY = 1
	PLAYING = 2
	state = INIT
	
	SETUP = 0
	PLAY = 1
	PAUSE = 2
	TEARDOWN = 3

	MAX_CACHE_FRAME_SIZE = 10
	
	# Initiation..
	def __init__(self, master, serveraddr, serverport, rtpport, filename):
		self.master = master
		self.master.protocol("WM_DELETE_WINDOW", self.handler)
		self.createWidgets()
		self.serverAddr = serveraddr
		self.serverPort = int(serverport)
		self.rtpPort = int(rtpport)
		self.fileName = filename
		self.rtspSeq = 0
		self.sessionId = 0
		self.requestSent = -1
		self.teardownAcked = 0
		self.connectToServer()
		self.frameNbr = 0
		self.currentFrame = -1
		
	def createWidgets(self):
		"""Build GUI."""
		# Create Setup button
		self.setup = Button(self.master, width=20, padx=3, pady=3)
		self.setup["text"] = "Setup"
		self.setup["command"] = self.setupMovie
		self.setup.grid(row=1, column=0, padx=2, pady=2)
		
		# Create Play button		
		self.start = Button(self.master, width=20, padx=3, pady=3)
		self.start["text"] = "Play"
		self.start["command"] = self.playMovie
		self.start.grid(row=1, column=1, padx=2, pady=2)
		
		# Create Pause button			
		self.pause = Button(self.master, width=20, padx=3, pady=3)
		self.pause["text"] = "Pause"
		self.pause["command"] = self.pauseMovie
		self.pause.grid(row=1, column=2, padx=2, pady=2)
		
		# Create Teardown button
		self.teardown = Button(self.master, width=20, padx=3, pady=3)
		self.teardown["text"] = "Teardown"
		self.teardown["command"] =  self.exitClient
		self.teardown.grid(row=1, column=3, padx=2, pady=2)
		
		# Create a label to display the movie
		self.label = Label(self.master, height=19)
		self.label.grid(row=0, column=0, columnspan=4, sticky=W+E+N+S, padx=5, pady=5) 
	
	def setupMovie(self):
		"""Setup button handler."""
		if self.state == self.INIT:
			self.sendRtspRequest(self.SETUP)
	
	def exitClient(self):
		"""Teardown button handler."""
		self.sendRtspRequest(self.TEARDOWN)		
		
		if hasattr(self, 'playbackStop'):
			self.playbackStop.set()
		self.master.destroy() # Close the gui window
		try:
			os.remove(CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT) # Delete the cache image from video
		except:
			pass

	def pauseMovie(self):
		"""Pause button handler."""
		if self.state == self.PLAYING:
			self.sendRtspRequest(self.PAUSE)
			if hasattr(self, 'playbackStop'):
				self.playbackStop.set()
	
	def playMovie(self):
		"""Play button handler."""
		if self.state == self.READY:
			# Create a new thread to listen for RTP packets
			threading.Thread(target=self.listenRtp).start()
			self.playEvent = threading.Event()
			self.playEvent.clear()
			self.sendRtspRequest(self.PLAY)
	
	def listenRtp(self):		
		"""Listen for RTP packets."""
		self.frameBuffer = bytearray() # Buffer để lưu trữ dữ liệu khung hình
		self.currentTimestamp = -1

		self.playbackBuffer = deque(maxlen = self.MAX_CACHE_FRAME_SIZE) #Buffer cho caching sử dụng queue
		self.playbackStop = threading.Event()
		threading.Thread(target=self._playbackLoop).start()

		self.rtpSocket.settimeout(0.5)

		while not self.playbackStop.is_set():
			try:
				data = self.rtpSocket.recv(20480) #Nhận gói từ Server
			except socket.timeout:
				continue
			except Exception:
				if self.teardownAcked == 1:
					self.rtpSocket.shutdown(socket.SHUT_RDWR)
					self.rtpSocket.close()
				break

			
			rtpPacket = RtpPacket()
			rtpPacket.decode(data)# Giải mã gói RTP nhận được (Lấy dữ liệu vào rtpPacket.header và rtpPacket.payload)
 
			timestamp = rtpPacket.timestamp()  # Cũng là số thứ tự khung hình (frame number)
			marker = rtpPacket.marker() # Lấy thông tin marker bit để xem đây có phải gói cuối cùng của khung hình hay không
			payload = rtpPacket.getPayload()

			# Nếu đây là frame mới thì ta reset buffer
			if self.currentTimestamp != timestamp:
				self.currentTimestamp = timestamp
				self.frameBuffer = bytearray()

			# Thêm payload vào buffer
			self.frameBuffer.extend(rtpPacket.getPayload())

			# Nếu marker bit = 1 thì đây là gói cuối cùng của frame
			if marker == 1:
				#gộp buffer lại thành frame
				#thêm frame vào buffer cho caching
				self.playbackBuffer.append(self.frameBuffer)
				#reset Buffer để nhận frame kế tiếp
				self.frameBuffer = bytearray()
			

		
		# while True:
		# 	try:
		# 		data = self.rtpSocket.recv(20480)
		# 		if data:
		# 			rtpPacket = RtpPacket()
		# 			rtpPacket.decode(data) # Giải mã gói RTP nhận được (Lấy dữ liệu vào rtpPacket.header và rtpPacket.payload)

		# 			seq = rtpPacket.seqNum()
		# 			timestamp = rtpPacket.timestamp() # Cũng là số thứ tự khung hình (frame number)
		# 			marker = rtpPacket.marker() # Lấy thông tin marker bit để xem đây có phải gói cuối cùng của khung hình hay không

		# 			print(f"Seq={seq}  Timestamp={timestamp}  Marker={marker}")
		# 			# Nếu đây là frame mới thì ta reset buffer
		# 			if timestamp != self.currentTimestamp:
		# 				self.currentTimestamp = timestamp
		# 				self.frameBuffer = bytearray()

		# 			# Thêm payload vào buffer
		# 			self.frameBuffer.extend(rtpPacket.getPayload())

		# 			# Nếu marker bit = 1 thì đây là gói cuối cùng của frame
		# 			if marker == 1:
        #             	# Cập nhật current frame index
		# 				self.frameNbr = timestamp

        #             	# Render
		# 				self.updateMovie(self.writeFrame(self.frameBuffer))

        #             	# Reset để nhận frame kế
		# 				self.currentTimestamp = -1
		# 				self.frameBuffer = bytearray()

		# 	except:
		# 		# Stop listening upon requesting PAUSE or TEARDOWN
		# 		if self.playEvent.is_set(): 
		# 			break
				
		# 		# Upon receiving ACK for TEARDOWN request,
		# 		# Close the RTP socket
		# 		if self.teardownAcked == 1:
		# 			self.rtpSocket.shutdown(socket.SHUT_RDWR)
		# 			self.rtpSocket.close()
		# 			break

	def _playbackLoop(self):
		"""Playback from buffer"""
		target_fps = 20 #fps mong muốn không quá lớn
		frame_interval = 1.0 / target_fps #Khoảng cách thời gian giữa các frame(s)
		last_time = time.time() #Thời điểm frame cuối cùng

		while not self.playbackStop.is_set():
			now = time.time()
			if now - last_time < frame_interval: #Đảm bảo thời gian đồng đều giữa các frame
				time.sleep(frame_interval - (now - last_time))
			last_time = time.time()

			if len(self.playbackBuffer) > 0: #kiểm tra có frame nào không
				try:
					frame = self.playbackBuffer.popleft()
					imageFile = self.writeFrame(frame)
					self.updateMovie(imageFile)
				except Exception as e:
					print(f"Playback error: {e}")
			else:
				time.sleep(0.1) #Đợi đến khi có frame trong buffer
					
	def writeFrame(self, data):
		"""Write the received frame to a temp image file. Return the image file."""
		# Tạo ra tên tệp tin cache dựa trên sessionId
		cachename = CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT
		# Ghi dữ liệu khung hình vào tệp tin
		file = open(cachename, "wb")
		file.write(data)
		file.close()
		# Trả về tên tệp tin cache
		return cachename
	#Sửa để stream video HD
	def updateMovie(self, imageFile):
		"""Update the image file as video frame in the GUI."""
		# Tạo biến photo với thông tin hình ảnh từ tệp tin imageFile(=tệp tin cache)
	
		# photo = ImageTk.PhotoImage(Image.open(imageFile))
		# self.label.configure(image = photo, height=288) 
		# self.label.image = photo

		#Mở hình jpeg
		img = Image.open(imageFile)

		original_width, original_height = img.size #Kích thước của hình gốc

		target_height = 480 #Chiều dài mong muốn

		aspect_ratio = original_width / original_height # Tỷ lệ của hình gốc
		target_width = int(target_height * aspect_ratio) #Chiểu rổng mong muốn 

		photo = ImageTk.PhotoImage(img)

		if target_height < original_height: #Nếu chiều dài hình lớn hơn mong muốn thì resize, scale hình có chất lượng tốt
			img_resized = img.resize((target_width, target_height), Image.LANCZOS)
			photo = ImageTk.PhotoImage(img_resized)
		else:
			target_width = original_width
			target_height = original_height
			

		self.label.configure(image = photo, width=target_width, height=target_height)
		self.label.image = photo
		
		
		
	def connectToServer(self):
		"""Connect to the Server. Start a new RTSP/TCP session."""
		self.rtspSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		try:
			self.rtspSocket.connect((self.serverAddr, self.serverPort))
		except:
			tkMessageBox.showwarning('Connection Failed', 'Connection to \'%s\' failed.' %self.serverAddr)
	
	def sendRtspRequest(self, requestCode):
		"""Send RTSP request to the server."""	
		
		# Setup request
		if requestCode == self.SETUP and self.state == self.INIT:
			threading.Thread(target=self.recvRtspReply).start()
			# Update RTSP sequence number.
			self.rtspSeq += 1
			
			# Write the RTSP request to be sent.
			request = "SETUP " + str(self.fileName) + " RTSP/1.0\nCSeq: " + str(self.rtspSeq) + "\nTransport: RTP/UDP; client_port= " + str(self.rtpPort)
			
			# Keep track of the sent request.
			self.requestSent = self.SETUP
		
		# Play request
		elif requestCode == self.PLAY and self.state == self.READY:
			# Update RTSP sequence number.
			self.rtspSeq += 1
			
			# Write the RTSP request to be sent.
			request = "PLAY " + str(self.fileName) + " RTSP/1.0\nCSeq: " + str(self.rtspSeq) + "\nSession: " + str(self.sessionId)
			
			# Keep track of the sent request.
			self.requestSent = self.PLAY
		
		# Pause request
		elif requestCode == self.PAUSE and self.state == self.PLAYING:
			# Update RTSP sequence number.
			self.rtspSeq += 1
			
			# Write the RTSP request to be sent.
			request = "PAUSE " + str(self.fileName) + " RTSP/1.0\nCSeq: " + str(self.rtspSeq) + "\nSession: " + str(self.sessionId)
			
			# Keep track of the sent request.
			self.requestSent = self.PAUSE
			
		# Teardown request
		elif requestCode == self.TEARDOWN and not self.state == self.INIT:
			# Update RTSP sequence number.
			self.rtspSeq += 1
			
			# Write the RTSP request to be sent.
			request = "TEARDOWN " + str(self.fileName) + " RTSP/1.0\nCSeq: " + str(self.rtspSeq) + "\nSession: " + str(self.sessionId)
			
			# Keep track of the sent request.
			self.requestSent = self.TEARDOWN
		else:
			return
		
		# Send the RTSP request using rtspSocket.
		self.rtspSocket.send(request.encode("utf-8"))
		
		print('\nData sent:\n' + request)
	
	def recvRtspReply(self):
		"""Receive RTSP reply from the server."""
		while True:
			reply = self.rtspSocket.recv(1024)
			
			if reply: 
				self.parseRtspReply(reply.decode("utf-8"))
			
			# Close the RTSP socket upon requesting Teardown
			if self.requestSent == self.TEARDOWN:
				self.rtspSocket.shutdown(socket.SHUT_RDWR)
				self.rtspSocket.close()
				break
	
	def parseRtspReply(self, data):
		"""Parse the RTSP reply from the server."""
		lines = data.split('\n')
		seqNum = int(lines[1].split(' ')[1])
		
		# Process only if the server reply's sequence number is the same as the request's
		if seqNum == self.rtspSeq:
			session = int(lines[2].split(' ')[1])
			# New RTSP session ID
			if self.sessionId == 0:
				self.sessionId = session
			
			# Process only if the session ID is the same
			if self.sessionId == session:
				if int(lines[0].split(' ')[1]) == 200: 
					if self.requestSent == self.SETUP:
						# Update RTSP state.
						self.state = self.READY
						
						# Open RTP port.
						self.openRtpPort() 
					elif self.requestSent == self.PLAY:
						self.state = self.PLAYING
					elif self.requestSent == self.PAUSE:
						self.state = self.READY
						
						# The play thread exits. A new thread is created on resume.
						self.playEvent.set()
					elif self.requestSent == self.TEARDOWN:
						self.state = self.INIT
						
						# Flag the teardownAcked to close the socket.
						self.teardownAcked = 1 
	
	def openRtpPort(self):
		"""Open RTP socket binded to a specified port."""
		# Create a new datagram socket to receive RTP packets from the server
		self.rtpSocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
		
		# Set the timeout value of the socket to 0.5sec
		self.rtpSocket.settimeout(0.5)
		
		try:
			# Bind the socket to the address using the RTP port given by the client user
			self.state = self.READY
			self.rtpSocket.bind(('', self.rtpPort))
		except:
			tkMessageBox.showwarning('Unable to Bind', 'Unable to bind PORT=%d' %self.rtpPort)

	def handler(self):
		"""Handler on explicitly closing the GUI window."""
		self.pauseMovie()
		if tkMessageBox.askokcancel("Quit?", "Are you sure you want to quit?"):
			self.exitClient()
		else: # When the user presses cancel, resume playing.
			self.playMovie()
