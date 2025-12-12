from random import randint
import sys, traceback, threading, socket, os, time

from VideoStream import VideoStream
from RtpPacket import RtpPacket

class ServerWorker:
    # RTSP Commands
    SETUP = 'SETUP'
    PLAY = 'PLAY'
    PAUSE = 'PAUSE'
    TEARDOWN = 'TEARDOWN'
    
    # Server States
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
        request = data.split('\n')
        line1 = request[0].split(' ')
        requestType = line1[0]
        filename = line1[1]
        seq = request[1].split(' ')
        
        # Process SETUP request
        if requestType == self.SETUP:
            if self.state == self.INIT:
                print("processing SETUP\n")
                
                try:
                    self.clientInfo['videoStream'] = VideoStream(filename)
                    self.state = self.READY
                except IOError:
                    self.replyRtsp(self.FILE_NOT_FOUND_404, seq[1])
                    return
                
                self.clientInfo['session'] = randint(100000, 999999)
                self.replyRtsp(self.OK_200, seq[1])
                self.clientInfo['rtpPort'] = request[2].split(' ')[3]
                
                # Tạo RTP socket sẵn
                self.clientInfo["rtpSocket"] = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        
        # Process PLAY request 		
        elif requestType == self.PLAY:
            if self.state == self.READY:
                print("processing PLAY\n")
                
                self.state = self.PLAYING
                
                # Đảm bảo có RTP socket
                if "rtpSocket" not in self.clientInfo:
                    self.clientInfo["rtpSocket"] = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                
                self.replyRtsp(self.OK_200, seq[1])
                
                # Tạo thread gửi RTP packets
                self.clientInfo['event'] = threading.Event()
                self.clientInfo['worker'] = threading.Thread(target=self.sendRtp)
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
            
            if 'event' in self.clientInfo:
                self.clientInfo['event'].set()
            
            self.replyRtsp(self.OK_200, seq[1])
            
            if 'rtpSocket' in self.clientInfo:
                self.clientInfo['rtpSocket'].close()

    def sendRtp(self):
        MAX_RTP_PAYLOAD = 1400

        fps = self.clientInfo['videoStream'].fps
        FRAME_INTERVAL = 1.0 / fps
        next_frame_time = time.time()
        address = self.clientInfo['rtspSocket'][1][0]
        port = int(self.clientInfo['rtpPort'])

        print(f"[Server] Streaming at {fps} FPS...")

        while True:
        # pause or teardown
            if self.clientInfo['event'].isSet(): 
                break

            now = time.time()
            if now < next_frame_time:
                time.sleep(next_frame_time - now)

            next_frame_time += FRAME_INTERVAL
            data = self.clientInfo['videoStream'].nextFrame()
            if not data:
                print("[Server] End of video.")
                
                # Gửi gói RTP rỗng báo EOS
                # encode(version, padding, extension, cc, seqnum, marker, pt, ssrc, payload, timestamp)
                eosPacket = RtpPacket()
                eosPacket.encode(2, 0, 0, 0, self.seqnum, 1, 26, 0, b'', 0)
                self.clientInfo['rtpSocket'].sendto(eosPacket.getPacket(), (address, port))
                print("[Server] Sent EOS packet")
                break

            timestamp = self.clientInfo['videoStream'].frameNbr()
            total_length = len(data)
            current_index = 0

            while current_index < total_length:
                payload_length = min(MAX_RTP_PAYLOAD, total_length - current_index)
                payload = data[current_index:current_index + payload_length]

                marker = 1 if current_index + payload_length >= total_length else 0

                self.seqnum += 1
                packet = self.makeRtp(payload, self.seqnum, marker, timestamp)

                self.clientInfo['rtpSocket'].sendto(packet, (address, port))

                current_index += payload_length


    def makeRtp(self, payload, seqnum, marker, timestamp):
        """Hàm hỗ trợ đóng gói RTP với các tham số cần thiết cho Phân gói."""
        version = 2
        padding = 0
        extension = 0
        cc = 0
        pt = 26
        ssrc = 0
		
        rtpPacket = RtpPacket()
        rtpPacket.encode(version, padding, extension, cc, seqnum, marker, pt, ssrc, payload, timestamp)
		
        return rtpPacket.getPacket()

    def replyRtsp(self, code, seq):
        """Send RTSP reply to the client."""
        if code == self.OK_200:
            reply = 'RTSP/1.0 200 OK\nCSeq: ' + seq + '\nSession: ' + str(self.clientInfo['session'])
            connSocket = self.clientInfo['rtspSocket'][0]
            connSocket.send(reply.encode())
        elif code == self.FILE_NOT_FOUND_404:
            print("404 NOT FOUND")
        elif code == self.CON_ERR_500:
            print("500 CONNECTION ERROR")