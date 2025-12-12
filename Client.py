""" 
=============================================================================
Client.py - RTSP/RTP Video Streaming Client
=============================================================================

CÁC THAY ĐỔI SO VỚI PHIÊN BẢN TRƯỚC:

1. ĐƠN GIẢN HÓA listenRtp():
   - TRƯỚC: Sử dụng defaultdict để lưu packets theo timestamp/seqNum,
            có logic sắp xếp lại packets, xử lý timeout, validate JPEG
   - SAU:   Giả sử packets luôn đến đúng thứ tự và đầy đủ,
            code đơn giản hơn ~50%

2. LOẠI BỎ CÁC HÀM PHỨC TẠP:
   - _tryAssembleFrame()     - ghép packet theo seqNum
   - _processCompletedFrames() - xử lý frame timeout  
   - _isValidJpeg()          - validate JPEG data

3. THÊM PRE-BUFFERING:
   - MIN_BUFFER_BEFORE_PLAY = 30 frames (0.5s) trước khi phát
   - _waitAndStartPlayback() đợi buffer đủ

4. TĂNG SOCKET BUFFER:
   - SO_RCVBUF = 8MB để nhận burst data từ server

5. ADAPTIVE FPS PLAYBACK:
   - Giảm FPS khi buffer thấp
   - Tạm dừng khi buffer critical
=============================================================================
"""

from tkinter import *
import tkinter.messagebox as tkMessageBox
from PIL import Image, ImageTk
import socket, threading, sys, traceback, os, time, io

from RtpPacket import RtpPacket
# THAY ĐỔI: Loại bỏ defaultdict - không cần nữa vì giả sử packets đúng thứ tự
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

	# ==========================================================================
	# CẤU HÌNH STREAMING - MỚI: Thêm các tham số điều khiển buffer và FPS
	# ==========================================================================
	TARGET_FPS = 60                    # FPS mục tiêu khi phát video
	MAX_CACHE_FRAME_SIZE = 180         # MỚI: Buffer lớn hơn (3 giây ở 60fps)
	MIN_BUFFER_BEFORE_PLAY = 30        # MỚI: Pre-buffer 30 frames trước khi phát
	LOW_BUFFER_THRESHOLD = 15          # MỚI: Giảm FPS khi buffer < 15
	CRITICAL_BUFFER_THRESHOLD = 5      # MỚI: Tạm dừng khi buffer < 5
	
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
		
		# Create a label to display the movie - KHÔNG đặt height cố định
		self.label = Label(self.master, bg='black')
		self.label.grid(row=0, column=0, columnspan=4, sticky=W+E+N+S, padx=5, pady=5)
		
		# Cho phép row 0 (video) co giãn
		self.master.grid_rowconfigure(0, weight=1)
		self.master.grid_columnconfigure(0, weight=1) 
	
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
			# Khởi tạo buffer
			self.playbackBuffer = deque(maxlen=self.MAX_CACHE_FRAME_SIZE)
			self.bufferReady = threading.Event()
			self.bufferReady.clear()
			self.playbackStop = threading.Event()
			self.framesReceived = 0
			
			# Bắt đầu lắng nghe RTP
			threading.Thread(target=self.listenRtp, daemon=True).start()
			
			self.playEvent = threading.Event()
			self.playEvent.clear()
			self.sendRtspRequest(self.PLAY)
			
			print(f"[Client] Đang buffer... cần {self.MIN_BUFFER_BEFORE_PLAY} frames trước khi phát")
	
	def listenRtp(self):		
		"""
		Lắng nghe RTP packets - PHIÊN BẢN ĐƠN GIẢN HÓA.
		
		THAY ĐỔI SO VỚI TRƯỚC:
		- TRƯỚC: Sử dụng packetBuffer (defaultdict) để lưu packets theo timestamp/seqNum
		         Có _tryAssembleFrame() sắp xếp lại packets
		         Có _processCompletedFrames() xử lý timeout
		         Có _isValidJpeg() validate JPEG data
		- SAU:   Giả sử packets luôn đến đúng thứ tự và đầy đủ
		         Chỉ cần ghép payload theo marker bit
		         Code đơn giản hơn ~50%
		
		Cơ chế hoạt động:
		1. Nhận packet RTP từ server
		2. Nếu timestamp mới → bắt đầu frame mới
		3. Ghép payload vào frameBuffer
		4. Nếu marker=1 → frame hoàn chỉnh → đẩy vào playbackBuffer
		"""
		self.frameBuffer = bytearray()  # Buffer tích lũy payload của frame hiện tại
		self.currentTimestamp = -1      # Timestamp của frame đang nhận
		
		# MỚI: Đợi buffer đủ rồi mới start playback (pre-buffering)
		threading.Thread(target=self._waitAndStartPlayback, daemon=True).start()

		self.rtpSocket.settimeout(0.1)  # Timeout ngắn để responsive

		while not self.playbackStop.is_set():
			try:
				data = self.rtpSocket.recv(65535)
			except socket.timeout:
				continue
			except Exception:
				if self.teardownAcked == 1:
					try:
						self.rtpSocket.shutdown(socket.SHUT_RDWR)
						self.rtpSocket.close()
					except:
						pass
				break

			# Decode RTP packet
			rtpPacket = RtpPacket()
			rtpPacket.decode(data)
 
			timestamp = rtpPacket.timestamp()
			marker = rtpPacket.marker()
			payload = rtpPacket.getPayload()

			# Nếu timestamp mới → frame mới → reset buffer
			if timestamp != self.currentTimestamp:
				self.currentTimestamp = timestamp
				self.frameBuffer = bytearray()

			# Thêm payload vào buffer
			self.frameBuffer.extend(payload)

			# Marker = 1 → packet cuối của frame → frame hoàn chỉnh
			if marker == 1:
				# Thêm frame vào playback buffer
				self.playbackBuffer.append(bytes(self.frameBuffer))
				self.framesReceived += 1
				
				# Kiểm tra đã đủ buffer chưa
				if not self.bufferReady.is_set() and len(self.playbackBuffer) >= self.MIN_BUFFER_BEFORE_PLAY:
					print(f"[Client] Buffer đủ! Bắt đầu phát video...")
					self.bufferReady.set()
				
				# Log tiến trình
				if self.framesReceived % 60 == 0:
					print(f"[Client] Đã nhận {self.framesReceived} frames, buffer: {len(self.playbackBuffer)}")
				
				# Reset frame buffer
				self.frameBuffer = bytearray()
	
	def _waitAndStartPlayback(self):
		"""Đợi buffer đủ rồi mới bắt đầu playback."""
		print(f"[Client] Đang chờ buffer đủ {self.MIN_BUFFER_BEFORE_PLAY} frames...")
		
		# Đợi đến khi buffer đủ hoặc bị dừng
		while not self.bufferReady.is_set() and not self.playbackStop.is_set():
			time.sleep(0.05)
			current = len(self.playbackBuffer)
			if current > 0 and current % 10 == 0:
				print(f"[Client] Buffering: {current}/{self.MIN_BUFFER_BEFORE_PLAY} frames")
		
		if not self.playbackStop.is_set():
			self._playbackLoop()
			

		
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
		"""Adaptive playback from buffer - điều chỉnh FPS dựa trên mức buffer."""
		base_fps = self.TARGET_FPS  # FPS mục tiêu (60)
		frames_played = 0
		start_time = time.time()
		last_time = time.time()
		
		print(f"[Playback] Bắt đầu phát ở {base_fps} FPS")

		while not self.playbackStop.is_set():
			buffer_level = len(self.playbackBuffer)
			
			# ====== ADAPTIVE FPS dựa trên buffer level ======
			if buffer_level <= self.CRITICAL_BUFFER_THRESHOLD:
				# Buffer quá thấp - tạm dừng chờ buffer
				if buffer_level == 0:
					print(f"[Playback] Buffer trống! Đang chờ...")
					time.sleep(0.1)
					continue
				# Buffer critical - giảm xuống 30 FPS
				current_fps = 30
			elif buffer_level <= self.LOW_BUFFER_THRESHOLD:
				# Buffer thấp - giảm xuống 45 FPS
				current_fps = 45
			else:
				# Buffer đủ - chạy full FPS
				current_fps = base_fps
			
			frame_interval = 1.0 / current_fps
			
			# Đảm bảo timing chính xác
			now = time.time()
			elapsed = now - last_time
			if elapsed < frame_interval:
				time.sleep(frame_interval - elapsed)
			last_time = time.time()

			if buffer_level > 0:
				try:
					frame = self.playbackBuffer.popleft()
					imageFile = self.writeFrame(frame)
					self.updateMovie(imageFile)
					frames_played += 1
					
					# In thống kê mỗi giây
					if frames_played % base_fps == 0:
						actual_time = time.time() - start_time
						actual_fps = frames_played / actual_time if actual_time > 0 else 0
						print(f"[Playback] Frames: {frames_played}, Buffer: {buffer_level}, FPS thực tế: {actual_fps:.1f}, FPS hiện tại: {current_fps}")
				except Exception as e:
					print(f"Playback error: {e}")
					
	def writeFrame(self, data):
		"""Write frame directly from memory, no file needed."""
		return data
	
	def updateMovie(self, frameData):
		"""Update video frame in GUI - resize chất lượng cao."""
		try:
			# Load JPEG trực tiếp từ memory
			img = Image.open(io.BytesIO(frameData))
			
			original_width, original_height = img.size
			
			# Kích thước hiển thị mong muốn (có thể thay đổi)
			MAX_DISPLAY_HEIGHT = 480  # 720p
			
			if original_height > MAX_DISPLAY_HEIGHT:
				# Tính kích thước mới giữ tỷ lệ
				scale = MAX_DISPLAY_HEIGHT / original_height
				new_width = int(original_width * scale)
				new_height = MAX_DISPLAY_HEIGHT
				
				# Resize với LANCZOS (chất lượng cao nhất)
				# Dùng Image.Resampling.LANCZOS cho Pillow mới
				try:
					img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
				except AttributeError:
					# Pillow cũ dùng Image.LANCZOS
					img = img.resize((new_width, new_height), Image.LANCZOS)
			
			# Convert sang PhotoImage
			photo = ImageTk.PhotoImage(img)
			
			# Update label
			self.label.configure(image=photo)
			self.label.image = photo  # Keep reference
			
		except Exception as e:
			pass
		
		
		
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
		"""
		Open RTP socket với buffer lớn để nhận burst data.
		
		THAY ĐỔI SO VỚI TRƯỚC:
		- TRƯỚC: Không set SO_RCVBUF (mặc định ~8KB-64KB tùy OS)
		- SAU:   SO_RCVBUF = 8MB để nhận burst data không mất gói
		"""
		self.rtpSocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
		
		# MỚI: Tăng receive buffer để nhận burst data từ server
		# Quan trọng khi server gửi nhiều frames cùng lúc
		self.rtpSocket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 * 1024 * 1024)  # 8MB
		
		self.rtpSocket.settimeout(0.5)
		
		try:
			self.state = self.READY
			self.rtpSocket.bind(('', self.rtpPort))
			
			# Kiểm tra buffer size thực tế
			actual_buffer = self.rtpSocket.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
			print(f"[Client] RTP socket bound, receive buffer: {actual_buffer / 1024 / 1024:.1f} MB")
		except:
			tkMessageBox.showwarning('Unable to Bind', 'Unable to bind PORT=%d' %self.rtpPort)

	def handler(self):
		"""Handler on explicitly closing the GUI window."""
		self.pauseMovie()
		if tkMessageBox.askokcancel("Quit?", "Are you sure you want to quit?"):
			self.exitClient()
		else: # When the user presses cancel, resume playing.
			self.playMovie()
