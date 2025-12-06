from email import header
import re
import struct
import os


class VideoStream:
	def __init__(self, filename):
		self.filename = filename
		try:
			# Mở file ở chế độ nhị phân (rb)
			self.file = open(filename, 'rb')
		except:
			raise IOError
		self.frameNum = 0
		# Byte đánh dấu cuối khung hình JPEG (End of Image - EOI)
		self.EOI = b'\xFF\xD9'
		self.format = self.detectFormat() # Xác định định dạng file
		print(f"VideoStream: Detected format = {self.format}")

	def detectFormat(self):
		self.file.seek(0)
		header = self.file.read(16)
		filesize = os.path.getsize(self.filename)
		self.file.seek(0)

		# 1. KIỂM TRA CUSTOM (ASCII digits đầu file)
		if re.match(rb'\d{1,8}', header[:8]):
			print("[Detect] CUSTOM length-prefixed (ASCII digits).")
			return "CUSTOM"

		# 2. KIỂM TRA CUSTOM (Binary 8-byte big-endian)
		try:
			val = struct.unpack("!Q", header[:8])[0]
			if 0 < val < filesize:
				print("[Detect] CUSTOM length-prefixed (Binary 8-byte).")
				return "CUSTOM"
		except:
			pass
		# 3. KIỂM TRA HEADERED (frameSize + frameNum)
		try:
			frameSize, frameNum = struct.unpack("!II", header[:8])
			self.file.seek(8)
			soi = self.file.read(2)
			self.file.seek(0)

			if 0 < frameSize < filesize and soi == b'\xFF\xD8':
				print("[Detect] HEADERED format.")
				return "HEADERED"
		except:
			pass

		# 4. MẶC ĐỊNH RAW
		print("[Detect] RAW MJPEG (split-by-EOI).")
		return "RAW"

	def nextFrame(self):
		"""Get next frame depending on detected format."""
		if self.format == "CUSTOM":
			return self.readCustomFrame()
		elif self.format == "HEADERED":
			return self.readHeaderedFrame()
		else:
			return self.readRawFrame()	
		
	# CÁCH 1 – FILE CUSTOM (ASCII digits + frame data)
	def readCustomFrame(self):
		header = self.file.read(8)
		if not header:
			return None

		# Try ASCII digits
		m = re.match(rb'(\d+)', header)
		if m:
			framelength = int(m.group(1))

			extra = header[m.end():]
			remaining = framelength - len(extra)
			data = extra + self.file.read(remaining)
			self.frameNum += 1
			return data

		# Try binary
		try:
			framelength = struct.unpack("!Q", header)[0]
		except:
			print("[ERROR] Invalid custom header:", header)
			return None

		data = self.file.read(framelength)
		self.frameNum += 1
		return data
	
	# CÁCH 2 – FILE DEMO CÓ HEADER 8 BYTE
	def readHeaderedFrame(self):
		header = self.file.read(8)
		if len(header) < 8:
			return None

		frameSize, frameNum = struct.unpack("!II", header)
		frameData = self.file.read(frameSize)

		if len(frameData) < frameSize:
			return None
		self.frameNum += 1
		return frameData

	# CÁCH 3 – RAW MJPEG (KHÔNG HEADER)
	def readRawFrame(self):
		"""Get next frame by searching for the JPEG End of Image (EOI) marker."""
		# Đọc dữ liệu từ vị trí hiện tại
		data = self.file.read(os.path.getsize(self.filename) - self.file.tell())

		if not data:
			return None # Hết tệp

		# Tìm vị trí của điểm kết thúc khung hình JPEG (0xFF D9)
		# Bắt đầu tìm kiếm ngay từ đầu dữ liệu đã đọc
		eoi_pos = data.find(self.EOI)

		if eoi_pos == -1:
			# Nếu không tìm thấy EOI, có thể là khung hình cuối cùng bị cắt
			# Hoặc tệp bị lỗi. Trả về None nếu không có thêm dữ liệu.
			if len(data) > 0:
				print("Warning: EOI marker not found in the last chunk of data.")
			return None

		# Vị trí của byte D9 là eoi_pos + 1. 
		# Khung hình hoàn chỉnh bao gồm cả 0xFF D9, nên lấy eoi_pos + 2
		frame_end_index = eoi_pos + 2

		# Dữ liệu khung hình là từ đầu cho đến EOI (bao gồm EOI)
		frame_data = data[:frame_end_index]

		# Cập nhật con trỏ file: di chuyển đến ngay sau EOI để sẵn sàng đọc khung hình tiếp theo
		self.file.seek(self.file.tell() - len(data) + frame_end_index)

		self.frameNum += 1
		return frame_data

	def frameNbr(self):
		"""Get frame number."""
		return self.frameNum