from random import randint
import sys, traceback, threading, socket

from VideoStream import VideoStream
from RtpPacket import RtpPacket
from time import time

class ServerWorker:
	SETUP = 'SETUP'
	PLAY = 'PLAY'
	PAUSE = 'PAUSE'
	TEARDOWN = 'TEARDOWN'
	
	INIT = 0
	READY = 1
	PLAYING = 2
	state = INIT

	OK_200 = 0
	FILE_NOT_FOUND_404 = 1
	CON_ERR_500 = 2
	
	clientInfo = {}
	
	def __init__(self, clientInfo):
		self.clientInfo = clientInfo
		self.seqnum = 0
		
	def run(self):
		threading.Thread(target=self.recvRtspRequest).start()
	
	def recvRtspRequest(self):
		"""Receive RTSP request from the client."""
		connSocket = self.clientInfo['rtspSocket'][0]
		while True:            
			data = connSocket.recv(256)
			if data:
				print("Data received:\n" + data.decode("utf-8"))
				self.processRtspRequest(data.decode("utf-8"))
	
	def processRtspRequest(self, data):
		"""Process RTSP request sent from the client."""
		# Get the request type
		request = data.split('\n')
		line1 = request[0].split(' ')
		requestType = line1[0]
		
		# Get the media file name
		filename = line1[1]
		
		# Get the RTSP sequence number 
		seq = request[1].split(' ')
		
		# Process SETUP request
		if requestType == self.SETUP:
			if self.state == self.INIT:
				# Update state
				print("processing SETUP\n")
				
				try:
					self.clientInfo['videoStream'] = VideoStream(filename)
					self.state = self.READY
				except IOError:
					self.replyRtsp(self.FILE_NOT_FOUND_404, seq[1])
				
				# Generate a randomized RTSP session ID
				self.clientInfo['session'] = randint(100000, 999999)
				
				# Send RTSP reply
				self.replyRtsp(self.OK_200, seq[1])
				
				# Get the RTP/UDP port from the last line
				self.clientInfo['rtpPort'] = request[2].split(' ')[3]
		
		# Process PLAY request 		
		elif requestType == self.PLAY:
			if self.state == self.READY:
				print("processing PLAY\n")
				self.state = self.PLAYING
				
				# Create a new socket for RTP/UDP
				self.clientInfo["rtpSocket"] = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
				
				self.replyRtsp(self.OK_200, seq[1])
				
				# Create a new thread and start sending RTP packets
				self.clientInfo['event'] = threading.Event()
				self.clientInfo['worker']= threading.Thread(target=self.sendRtp) 
				self.clientInfo['worker'].start()
		
		# Process PAUSE request
		elif requestType == self.PAUSE:
			if self.state == self.PLAYING:
				print("processing PAUSE\n")
				self.state = self.READY
				
				self.clientInfo['event'].set()
			
				self.replyRtsp(self.OK_200, seq[1])
		
		# Process TEARDOWN request
		elif requestType == self.TEARDOWN:
			print("processing TEARDOWN\n")

			self.clientInfo['event'].set()
			
			self.replyRtsp(self.OK_200, seq[1])
			
			# Close the RTP socket
			self.clientInfo['rtpSocket'].close()
			
	# Được gọi ở hàm xử lý PLAY		
	def sendRtp(self):
		"""Send RTP packets over UDP."""
		MAX_RTP_PAYLOAD = 1400
		
		while True:
			self.clientInfo['event'].wait(0.05) 
			
			# Stop sending if request is PAUSE or TEARDOWN
			if self.clientInfo['event'].isSet(): 
				break 
				
			# Lấy toàn bộ khung hình (ví dụ: 50 KB)	
			data = self.clientInfo['videoStream'].nextFrame()
			# Nếu còn dữ liệu khung hình
			if data: 
				# Lấy số thứ tự khung hình (sẽ là Timestamp)
				frameNumber = self.clientInfo['videoStream'].frameNbr()
				timestamp = frameNumber

				current_index = 0
				total_length = len(data)

				address = self.clientInfo['rtspSocket'][1][0]
				port = int(self.clientInfo['rtpPort'])

				while current_index < total_length:
            		# 1. Xác định kích thước Payload cho gói tin hiện tại
					payload_length = min(MAX_RTP_PAYLOAD, total_length - current_index)
					payload = data[current_index:current_index + payload_length]
            
            		# 2. Xác định cờ Marker: Chỉ gói cuối cùng mới có M=1
					marker_bit = 0
					if current_index + payload_length >= total_length:
                		# Đây là gói tin cuối cùng của khung hình
						marker_bit = 1
                
            		# 3. Tăng Sequence Number cho MỖI GÓI TIN RTP
					self.seqnum += 1 
            
            		# 4. Tạo và đóng gói gói tin RTP
					packet = self.makeRtp(payload, self.seqnum, marker_bit, timestamp)
            
           			# 5. Gửi gói tin
					try:
						self.clientInfo['rtpSocket'].sendto(packet, (address, port))
					except Exception as e:
						# ... (xử lý lỗi gửi) ...
						print("Connection Error")
						break
                
            		# Cập nhật chỉ mục cho lần lặp tiếp theo
					current_index += payload_length
			else:
				print("End of video.") # Dừng phát khi hết video
				break
			
	def makeRtp(self, payload, seqnum, marker, timestamp):
		"""Hàm hỗ trợ đóng gói RTP với các tham số cần thiết cho Phân gói."""
		# Các hằng số cố định theo đồ án (V=2, PT=26, P=0, X=0, CC=0)
		version = 2
		padding = 0
		extension = 0
		cc = 0
		pt = 26 # MJPEG type
		ssrc = 0
		
		rtpPacket = RtpPacket()
		
		# Gọi encode với đầy đủ tham số
		# SeqNum, Marker, Timestamp và SSRC được truyền từ bên ngoài
		rtpPacket.encode(version, padding, extension, cc, seqnum, marker, pt, ssrc, payload, timestamp)
		
		return rtpPacket.getPacket()

	def replyRtsp(self, code, seq):
		"""Send RTSP reply to the client."""
		if code == self.OK_200:
			#print("200 OK")
			reply = 'RTSP/1.0 200 OK\nCSeq: ' + seq + '\nSession: ' + str(self.clientInfo['session'])
			connSocket = self.clientInfo['rtspSocket'][0]
			connSocket.send(reply.encode())
		
		# Error messages
		elif code == self.FILE_NOT_FOUND_404:
			print("404 NOT FOUND")
		elif code == self.CON_ERR_500:
			print("500 CONNECTION ERROR")
