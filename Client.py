from tkinter import *
import tkinter.messagebox as tkMessageBox
from PIL import Image, ImageTk
import socket, threading, time, io, os

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

	# CẤU HÌNH STREAMING
	TARGET_FPS = 40                    # FPS mục tiêu khi phát video
	MIN_BUFFER_BEFORE_PLAY = 30        # Pre-buffer 30 frames trước khi phát
	MAX_BUFFER_SIZE = 300              # Buffer tối đa - tạm dừng nhận khi đạt
	MIN_BUFFER_TO_RESUME = 100         # Buffer tối thiểu để tiếp tục nhận
	
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

		self.playbackBuffer = deque()  # Chứa các frame hoàn chỉnh để phát
		self.bufferLock = threading.Lock()
		self.eosReceived = False
		self.framesReceived = 0
		self.bufferReady = threading.Event()
		self.playbackStop = threading.Event()
		self.playEvent = threading.Event()
		self.bufferPaused = False  # Cờ tạm dừng nhận khi buffer đầy
		self._playbackActive = False  # Cờ playback đang chạy
		self._bufferPlaybackActive = False  # Cờ buffer playback đang chạy

		
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
		self.label = Label(self.master)
		self.label.grid(row=0, column=0, columnspan=4, sticky=W+E+N+S, padx=5, pady=5)

	
	def setupMovie(self):
		"""Setup button handler."""
		if self.state == self.INIT:
			self.eosReceived = False  # Reset EOS flag
			self.playbackBuffer.clear()  # Reset playback buffer
			self.framesReceived = 0   # Reset frame counter
			self.bufferReady.clear()  # Reset buffer ready event
			self.playbackStop.clear() # Reset playback stop event
			self.bufferPaused = False # Reset buffer pause flag
			self.sendRtspRequest(self.SETUP)
	
	def exitClient(self):
		"""Teardown button handler."""
		self.sendRtspRequest(self.TEARDOWN)		
		
		# Dừng tất cả các threads
		if hasattr(self, 'playbackStop'):
			self.playbackStop.set()
		if hasattr(self, 'playEvent'):
			self.playEvent.set()
		if hasattr(self, 'bufferReady'):
			self.bufferReady.set()  # Unblock waiting threads
		
		# Đóng RTP socket để unblock recvfrom
		if hasattr(self, 'rtpSocket'):
			try:
				self.rtpSocket.close()
			except:
				pass
				
		self.master.destroy() # Close the gui window
		try:
			os.remove(CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT) # Delete the cache image from video
		except:
			pass

	def pauseMovie(self):
		"""Pause button handler."""
		if self.state == self.PLAYING:
			# Dừng playback ngay lập tức
			self._playbackActive = False
			self._bufferPlaybackActive = False
			
			# Nếu đã nhận EOS (server đã dừng), chỉ cần dừng playback ở client
			# Không cần gửi PAUSE request vì server đã không còn gửi data
			if not self.eosReceived:
				self.sendRtspRequest(self.PAUSE)
			else:
				# Dừng playback thread và chuyển về READY
				self.state = self.READY
				if hasattr(self, 'playEvent'):
					self.playEvent.set()
				print(f"[Client] Paused locally (server đã hết video)")
				
			with self.bufferLock:
				print(f"[Client] Paused. Buffer còn {len(self.playbackBuffer)} frames")
	
	def playMovie(self):
		"""Play button handler."""
		if self.state == self.READY or self.state == self.PLAYING:
			# Kiểm tra buffer hiện tại trước
			with self.bufferLock:
				currentBuffer = len(self.playbackBuffer)
			
			# Nếu đã nhận EOS (server hết video)
			if self.eosReceived:
				if currentBuffer > 0:
					# Còn frames trong buffer -> phát những gì còn lại
					print(f"[Client] Server đã hết video, phát {currentBuffer} frames còn trong buffer")
					# Chuyển sang PLAYING để playback thread chạy
					self.state = self.PLAYING
					self.playEvent.clear()  # Cho phép playback chạy
					if not hasattr(self, 'playbackThread') or not self.playbackThread.is_alive():
						self.playbackThread = threading.Thread(target=self.playbackFromBuffer)
						self.playbackThread.start()
				else:
					# Buffer rỗng và server đã hết video -> thông báo
					print(f"[Client] Video đã kết thúc, không còn frame nào trong buffer")
				return  # KHÔNG gửi PLAY request khi đã EOS
			
			# Gửi PLAY request - playback sẽ được xử lý bởi _playbackLoop trong listenRtp
			self.sendRtspRequest(self.PLAY)
			print(f"[Client] Đang chờ buffer đủ {self.MIN_BUFFER_BEFORE_PLAY} frames")
	
	def playbackFromBuffer(self):
		"""PLAY VIDEO FROM BUFFER"""
		base_fps = 30
		frames_played = 0
		frame_interval = 1.0 / base_fps
		
		self._bufferPlaybackActive = True
		
		while self._bufferPlaybackActive and self.state == self.PLAYING and not self.playEvent.is_set():
			start_time = time.time()
			
			with self.bufferLock:
				bufferSize = len(self.playbackBuffer)
			
			# Nếu buffer rỗng và đã nhận EOS -> kết thúc
			if bufferSize == 0 and self.eosReceived:
				print("[Client] Đã phát hết video")
				self.state = self.READY
				break
				
			# Nếu buffer rỗng nhưng chưa EOS -> chờ thêm data
			if bufferSize == 0:
				time.sleep(0.01)
				continue
				
			# Lấy frame từ buffer và hiển thị
			with self.bufferLock:
				if self.playbackBuffer:
					frame = self.playbackBuffer.popleft()
			
			try:
				imageFile = self.writeFrame(frame)
				self.updateMovie(imageFile)
				frames_played += 1
				if frames_played % 30 == 0:
					print(f"[Playback from buffer] Frames: {frames_played}, Buffer còn: {len(self.playbackBuffer)}")
			except Exception as e:
				print(f"[Playback error] {e}")
			
			# Đảm bảo timing chính xác
			elapsed = time.time() - start_time
			if elapsed < frame_interval:
				time.sleep(frame_interval - elapsed)
		
		print(f"[Playback from buffer] Đã phát {frames_played} frames")
		self._bufferPlaybackActive = False

	def listenRtp(self):		
		"""Listen for RTP packets"""
		self._frameAssemblyBuffer = bytearray()  # Buffer tích lũy payload của frame hiện tại
		self.currentTimestamp = -1      # Timestamp của frame đang nhận
		
		# MỚI: Đợi buffer đủ rồi mới start playback (pre-buffering)
		threading.Thread(target=self._waitAndStartPlayback, daemon=True).start()

		self.rtpSocket.settimeout(0.1)  # Timeout ngắn để responsive

		while True:
			# Kiểm tra điều kiện thoát
			if self.teardownAcked == 1 or self.playEvent.is_set():
				print("[Client] RTP listener stopped")
				break
				
			try:
				data, addr = self.rtpSocket.recvfrom(65535)
				if data:
					rtpPacket = RtpPacket()
					rtpPacket.decode(data)
					
					# Kiểm tra marker bit hoặc payload rỗng = EOS
					payload = rtpPacket.getPayload()
					if len(payload) == 0:
						print("[Client] Nhận được tín hiệu End-of-Stream từ server")
						self.eosReceived = True
						continue
						
					timestamp = rtpPacket.timestamp()
					marker = rtpPacket.marker()
					
					# Nếu timestamp mới → frame mới → reset buffer
					if timestamp != self.currentTimestamp:
						self.currentTimestamp = timestamp
						self._frameAssemblyBuffer = bytearray()

					# Thêm payload vào buffer
					self._frameAssemblyBuffer.extend(payload)

					# Marker = 1 → packet cuối của frame → frame hoàn chỉnh
					if marker == 1:
						# Thêm frame vào playback buffer (thread-safe)
						with self.bufferLock:
							buffer_size = len(self.playbackBuffer)
							
							# Kiểm tra buffer đầy -> tạm dừng nhận
							if buffer_size >= self.MAX_BUFFER_SIZE:
								if not self.bufferPaused:
									self.bufferPaused = True
									print(f"[Client] Buffer đầy ({buffer_size} frames), tạm dừng nhận...")
								# Không thêm frame mới khi buffer đầy
								self._frameAssemblyBuffer = bytearray()
								continue
							
							# Kiểm tra buffer đã giảm xuống -> tiếp tục nhận
							if self.bufferPaused and buffer_size <= self.MIN_BUFFER_TO_RESUME:
								self.bufferPaused = False
								print(f"[Client] Buffer giảm xuống {buffer_size}, tiếp tục nhận...")
							
							# Thêm frame vào buffer nếu không bị pause
							if not self.bufferPaused:
								self.playbackBuffer.append(bytes(self._frameAssemblyBuffer))
								self.framesReceived += 1
						
						# Kiểm tra đã đủ buffer chưa để bắt đầu phát
						if not self.bufferReady.is_set() and len(self.playbackBuffer) >= self.MIN_BUFFER_BEFORE_PLAY:
							print(f"[Client] Buffer đủ! Bắt đầu phát video...")
							self.bufferReady.set()
						
						# Log tiến trình
						if self.framesReceived % 60 == 0:
							print(f"[Client] Đã nhận {self.framesReceived} frames, buffer: {len(self.playbackBuffer)}")
						
						# Reset frame buffer
						self._frameAssemblyBuffer = bytearray()
			except socket.timeout:
				# Timeout khi chờ data - tiếp tục vòng lặp
				continue
			except OSError:
				# Socket đã đóng
				break
			except Exception as e:
				# Lỗi khác - kiểm tra teardown
				if self.teardownAcked == 1:
					break
				if self.playEvent.is_set():
					break
	
	def _waitAndStartPlayback(self):
		"""Wait until buffer size reach MINIMUM SIZE BEFORE PLAY"""
		print(f"[Client] Đang chờ buffer đủ {self.MIN_BUFFER_BEFORE_PLAY} frames...")
		
		# Đợi đến khi buffer đủ hoặc bị dừng
		while not self.bufferReady.is_set() and not self.playbackStop.is_set():
			time.sleep(0.05)
			current = len(self.playbackBuffer)
			if current > 0 and current % 10 == 0:
				print(f"[Client] Buffering: {current}/{self.MIN_BUFFER_BEFORE_PLAY} frames")
		
		if not self.playbackStop.is_set():
			self._playbackLoop()

	def _playbackLoop(self):
		"""Khởi động playback loop trên main thread."""
		self._playbackFps = self.TARGET_FPS
		self._playbackFramesPlayed = 0
		self._playbackStartTime = time.time()
		self._playbackActive = True
		
		print(f"[Playback] Bắt đầu phát ở {self._playbackFps} FPS")
		# Bắt đầu vòng lặp playback trên main thread
		self.master.after(0, self._playbackTick)
	
	def _playbackTick(self):
		"""Một tick của playback - chạy trên main thread."""
		if not self._playbackActive:
			return
			
		# Kiểm tra điều kiện dừng
		if self.playbackStop.is_set() or self.playEvent.is_set():
			self._playbackActive = False
			return
		
		buffer_level = len(self.playbackBuffer)
		
		# Nếu buffer rỗng và đã EOS -> kết thúc playback
		if buffer_level == 0 and self.eosReceived:
			print(f"[Playback] Video kết thúc. Đã phát {self._playbackFramesPlayed} frames")
			self.state = self.READY
			self._playbackActive = False
			return
		
		# Nếu buffer rỗng nhưng chưa EOS -> chờ thêm data
		if buffer_level == 0:
			self.master.after(10, self._playbackTick)
			return

		try:
			frame = self.playbackBuffer.popleft()
			imageFile = self.writeFrame(frame)
			self.updateMovie(imageFile)  # Gọi trực tiếp vì đang ở main thread
			self._playbackFramesPlayed += 1
			
			# In thống kê mỗi giây
			if self._playbackFramesPlayed % self._playbackFps == 0:
				actual_time = time.time() - self._playbackStartTime
				actual_fps = self._playbackFramesPlayed / actual_time if actual_time > 0 else 0
				print(f"[Playback] Frames: {self._playbackFramesPlayed}, Buffer: {buffer_level}, FPS thực tế: {actual_fps:.1f}")
		except Exception as e:
			print(f"Playback error: {e}")
		
		# Schedule tick tiếp theo
		frame_interval_ms = int(1000 / self._playbackFps)
		self.master.after(frame_interval_ms, self._playbackTick)
					
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
		try:
			self.rtspSocket.send(request.encode("utf-8"))
			print('\nData sent:\n' + request)
		except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, OSError) as e:
			print(f"[Client] Không thể gửi request: {e}")
			# Server đã ngắt kết nối
			if requestCode == self.TEARDOWN:
				# Nếu đang teardown thì coi như thành công
				self.teardownAcked = 1
			self._handleServerDisconnect()
	
	def recvRtspReply(self):
		"""Receive RTSP reply from the server."""
		while True:
			try:
				reply = self.rtspSocket.recv(20480)
				
				if reply: 
					self.parseRtspReply(reply.decode("utf-8"))
				else:
					# Server đóng kết nối (recv trả về empty)
					print("[Client] Server đã đóng kết nối")
					self._handleServerDisconnect()
					break
				
				# Close the RTSP socket upon requesting Teardown
				if self.requestSent == self.TEARDOWN:
					self.rtspSocket.shutdown(socket.SHUT_RDWR)
					self.rtspSocket.close()
					break
					
			except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
				# Server ngắt kết nối đột ngột
				print("[Client] Mất kết nối với server")
				self._handleServerDisconnect()
				break
			except OSError as e:
				# Socket đã đóng hoặc lỗi khác
				if self.requestSent != self.TEARDOWN:
					print(f"[Client] Lỗi kết nối: {e}")
					self._handleServerDisconnect()
				break
	
	def _handleServerDisconnect(self):
		"""Xử lý khi mất kết nối với server."""
		# Đánh dấu EOS để playback biết không còn data mới
		self.eosReceived = True
		
		# Nếu còn frames trong buffer, tiếp tục phát
		with self.bufferLock:
			remaining = len(self.playbackBuffer)
		
		if remaining > 0:
			print(f"[Client] Còn {remaining} frames trong buffer, tiếp tục phát...")
		else:
			print("[Client] Buffer rỗng, dừng video")
			self.state = self.READY
	
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
						
						# Start RTP receiving thread nếu chưa chạy
						if not hasattr(self, 'rtpThread') or not self.rtpThread.is_alive():
							self.playEvent.clear()
							self.rtpThread = threading.Thread(target=self.listenRtp)
							self.rtpThread.start()
					elif self.requestSent == self.PAUSE:
						self.state = self.READY
						
						# The play thread exits. A new thread is created on resume.
						self.playEvent.set()
					elif self.requestSent == self.TEARDOWN:
						self.state = self.INIT
						
						# Flag the teardownAcked to close the socket.
						self.teardownAcked = 1 
	
	def openRtpPort(self):
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
