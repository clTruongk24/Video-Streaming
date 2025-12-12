""" 
=============================================================================
VideoStream.py - MJPEG Video File Reader
=============================================================================

CÁC THAY ĐỔI SO VỚI PHIÊN BẢN TRƯỚC:

1. CẢI TIẾN readRawFrame():
   - TRƯỚC: Đọc toàn bộ file còn lại vào RAM, tính sai vị trí file pointer
   - SAU:   Đọc theo chunk 64KB, lưu start_pos trước khi đọc,
            tính đúng vị trí seek sau khi extract frame

2. CẢI TIẾN detectFormat():
   - Thêm kiểm tra RAW MJPEG (bắt đầu trực tiếp bằng SOI marker)
   - Cải thiện phát hiện CUSTOM format (ASCII length prefix)

3. THÊM validateJpeg():
   - Kiểm tra SOI và EOI markers
   - Verify bằng PIL Image

4. CẢI TIẾN readCustomFrame():
   - Hỗ trợ header size động (customHeaderSize)
   - Có validation sau khi đọc
=============================================================================
"""

from email import header
import re
import struct
import os
from PIL import Image
import io


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
		# JPEG Start of Image marker
		self.SOI = b'\xFF\xD8'
		self.format = self.detectFormat() # Xác định định dạng file
		print(f"VideoStream: Detected format = {self.format}")

	def detectFormat(self):
		"""Phát hiện định dạng file MJPEG."""
		self.file.seek(0)
		header = self.file.read(20)
		filesize = os.path.getsize(self.filename)
		self.file.seek(0)

		# 1. KIỂM TRA RAW MJPEG - bắt đầu trực tiếp bằng JPEG SOI (0xFF 0xD8)
		if header[0:2] == self.SOI:
			print("[Detect] RAW MJPEG (starts with SOI marker).")
			return "RAW"

		# 2. KIỂM TRA CUSTOM format (5 byte ASCII digits + JPEG data) - như movie.Mjpeg
		# Ví dụ: "06014" + [JPEG data 6014 bytes]
		if header[0:1].isdigit():
			# Tìm vị trí bắt đầu của JPEG (SOI marker)
			soi_pos = header.find(self.SOI)
			if soi_pos > 0 and soi_pos <= 8:
				# Lấy độ dài từ header
				length_str = header[:soi_pos]
				if length_str.isdigit():
					print(f"[Detect] CUSTOM format: {soi_pos}-byte length prefix (ASCII).")
					self.customHeaderSize = soi_pos
					return "CUSTOM"

		# 3. KIỂM TRA HEADERED (8-byte binary: frameSize + frameNum)
		try:
			frameSize, frameNum = struct.unpack("!II", header[:8])
			if 0 < frameSize < filesize and header[8:10] == self.SOI:
				print("[Detect] HEADERED format (8-byte binary header).")
				return "HEADERED"
		except:
			pass

		# 4. MẶC ĐỊNH RAW nếu không xác định được
		print("[Detect] Defaulting to RAW MJPEG.")
		return "RAW"

	def nextFrame(self):
		"""Get next frame depending on detected format."""
		if self.format == "CUSTOM":
			return self.readCustomFrame()
		elif self.format == "HEADERED":
			return self.readHeaderedFrame()
		else:
			return self.readRawFrame()
	
	def validateJpeg(self, data):
		"""Kiểm tra và validate JPEG data."""
		if not data or len(data) < 4:
			return None
		
		# Kiểm tra SOI và EOI markers
		if data[0:2] != self.SOI:
			print(f"[WARN] Frame missing SOI marker, got: {data[0:2].hex()}")
			return None
		
		if data[-2:] != self.EOI:
			print(f"[WARN] Frame missing EOI marker, got: {data[-2:].hex()}")
			return None
		
		# Thử decode để verify
		try:
			img = Image.open(io.BytesIO(data))
			img.verify()
			return data
		except Exception as e:
			print(f"[WARN] Invalid JPEG data: {e}")
			return None
		
	# CÁCH 1 – FILE CUSTOM (ASCII digits + JPEG data) như movie.Mjpeg
	def readCustomFrame(self):
		"""Đọc frame từ file có header là ASCII digits chỉ độ dài."""
		# Đọc header (số byte tùy thuộc vào format đã detect)
		headerSize = getattr(self, 'customHeaderSize', 5)
		header = self.file.read(headerSize)
		
		if not header or len(header) < headerSize:
			return None

		# Parse độ dài frame từ ASCII digits
		try:
			framelength = int(header.decode('ascii'))
		except (ValueError, UnicodeDecodeError):
			print(f"[ERROR] Invalid custom header: {header}")
			return None

		# Đọc đúng số byte của frame
		data = self.file.read(framelength)
		
		if len(data) < framelength:
			print(f"[WARN] Incomplete frame: expected {framelength}, got {len(data)}")
			return None
		
		self.frameNum += 1
		
		# Validate JPEG
		validated = self.validateJpeg(data)
		if validated is None:
			print(f"[WARN] Frame {self.frameNum} failed validation, skipping...")
			# Thử đọc frame tiếp theo
			return self.readCustomFrame()
		
		return validated
	
	# CÁCH 2 – FILE DEMO CÓ HEADER 8 BYTE (binary)
	def readHeaderedFrame(self):
		header = self.file.read(8)
		if len(header) < 8:
			return None

		frameSize, frameNum = struct.unpack("!II", header)
		frameData = self.file.read(frameSize)

		if len(frameData) < frameSize:
			return None
		
		self.frameNum += 1
		return self.validateJpeg(frameData) or frameData

	# CÁCH 3 – RAW MJPEG (scan for SOI and EOI markers)
	def readRawFrame(self):
		"""
		Đọc frame từ RAW MJPEG bằng cách tìm SOI và EOI markers.
		
		THAY ĐỔI SO VỚI TRƯỚC:
		- TRƯỚC: Đọc toàn bộ file còn lại vào RAM
		         Dùng self.file.tell() sau khi đọc (sai vị trí!)
		- SAU:   Đọc theo chunk 64KB (tiết kiệm RAM)
		         Lưu start_pos TRƯỚC khi đọc
		         Tính đúng: frame_start + frame_end để seek
		
		Cơ chế:
		1. Tìm SOI marker (0xFF 0xD8) - bắt đầu JPEG
		2. Từ SOI, tìm EOI marker (0xFF 0xD9) - kết thúc JPEG  
		3. Extract dữ liệu từ SOI đến EOI
		4. Seek file pointer về sau EOI để đọc frame tiếp
		"""
		# MỚI: Đọc theo chunk thay vì toàn bộ file
		CHUNK_SIZE = 65536  # 64KB chunks - tiết kiệm RAM
		
		# Tìm SOI marker (start of JPEG)
		start_pos = self.file.tell()
		
		# Đọc để tìm SOI
		data = self.file.read(CHUNK_SIZE)
		if not data:
			return None
		
		# Tìm SOI trong data
		soi_pos = data.find(self.SOI)
		if soi_pos == -1:
			# Không tìm thấy SOI, đọc tiếp
			return None
		
		# Bắt đầu từ SOI, tìm EOI
		frame_start = start_pos + soi_pos
		self.file.seek(frame_start)
		
		# Đọc đủ data để tìm EOI
		frame_data = bytearray()
		max_frame_size = 5 * 1024 * 1024  # Max 5MB per frame
		
		while len(frame_data) < max_frame_size:
			chunk = self.file.read(CHUNK_SIZE)
			if not chunk:
				break
			
			frame_data.extend(chunk)
			
			# Tìm EOI trong data đã có
			eoi_pos = frame_data.find(self.EOI)
			if eoi_pos != -1:
				# Tìm thấy EOI - cắt frame
				frame_end = eoi_pos + 2
				result = bytes(frame_data[:frame_end])
				
				# Seek về vị trí sau EOI
				self.file.seek(frame_start + frame_end)
				
				self.frameNum += 1
				return self.validateJpeg(result) or result
		
		# Không tìm thấy EOI
		print(f"[WARN] EOI not found for frame starting at {frame_start}")
		return None

	def frameNbr(self):
		"""Get frame number."""
		return self.frameNum