import sys
from tkinter import Tk
from Client import Client

if __name__ == "__main__":
	try:
		serverAddr = sys.argv[1]
		serverPort = sys.argv[2]
		rtpPort = sys.argv[3]
		fileName = sys.argv[4]	
	except:
		print("[Usage: ClientLauncher.py Server_name Server_port RTP_port Video_file]\n")	

	# Root là cửa sổ chính của ứng dụng Tkinter
	# Client được tạo sẽ dùng root để gắn các widget như button, label, canvas… lên cửa sổ chính.
	root = Tk()
	
	# Create a new client
	app = Client(root, serverAddr, serverPort, rtpPort, fileName) # Tạo một đối tượng Client với các tham số đã cung cấp
	app.master.title("RTPClient")	# Đặt tiêu đề cho cửa sổ chính
	root.mainloop() # Khởi động vòng lặp chính của giao diện Tkinter để lắng nghe và xử lý các sự kiện: bấm nút, đóng cửa sổ…
	