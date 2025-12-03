import re
import struct


class VideoStream:
	def __init__(self, filename):
		self.filename = filename
		try:
			self.file = open(filename, 'rb')
		except:
			raise IOError
		self.frameNum = 0

	def nextFrame(self):
		"""Get next frame.

		This function supports two common framing encodings seen in simple
		MJPEG container demos:
		- ASCII decimal length at start of frame (may be shorter than 8 bytes,
		  the remaining header bytes can contain the beginning of the frame),
		- 8-byte big-endian unsigned integer (binary length).

		It attempts to detect ASCII digits at the start of the 8-byte header;
		if found it uses those digits as the length and preserves any extra
		bytes read from the header as the start of the frame payload. If no
		ASCII digits are present it falls back to unpacking an 8-byte big-endian
		integer.
		"""
		header = self.file.read(8)
		if not header:
			return None

		# Try to parse leading ASCII digits (e.g. b'00012345')
		m = re.match(rb'(\d+)', header)
		if m:
			framelength = int(m.group(1))

			# Any bytes in the header after the digits are part of the frame
			extra = header[m.end():]
			remaining = framelength - len(extra)
			if remaining > 0:
				rest = self.file.read(remaining)
				data = extra + rest
			else:
				# All frame bytes were already read into 'extra'
				data = extra[:framelength]

			self.frameNum += 1
			return data

		# Fallback: try binary 8-byte big-endian unsigned long long
		try:
			framelength = struct.unpack('!Q', header)[0]
		except Exception:
			# Last resort: try to decode ASCII with strip
			try:
				framelength = int(header.decode('ascii').strip())
			except Exception:
				raise ValueError(f"Could not parse frame length header: {header!r}")

		data = self.file.read(framelength)
		self.frameNum += 1
		return data

	def frameNbr(self):
		"""Get frame number."""
		return self.frameNum