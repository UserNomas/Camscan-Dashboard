pkg install -y nmap ffmpeg curl grep sed awk && \
cat > camscan.sh <<'EOF'
#!/data/data/com.termux/files/usr/bin/bash

NET="192.168.100.0/24"

echo "========================================="
echo " CAMERA DISCOVERY"
echo "========================================="

for IP in $(nmap -sn $NET | awk '/Nmap scan report/{print $NF}')
do

echo
echo "===== $IP ====="

PORTS=$(nmap -Pn --min-rate 1000 -p80,554,5000,8000,8080,8899 $IP \
2>/dev/null | awk '/open/{print $1}')

[ -z "$PORTS" ] && continue

echo "$PORTS"

if echo "$PORTS" | grep -q "80/tcp"; then
    TITLE=$(curl -m 3 -s http://$IP | grep -i "<title>" | head -1)
    echo "HTTP: $TITLE"
fi

if echo "$PORTS" | grep -q "554/tcp"; then

for PATH in \
"/Streaming/Channels/101" \
"/Streaming/Channels/102" \
"/live/ch00_0" \
"/live/ch00_1" \
"/live" \
"/h264" \
"/ch0.h264" \
"/ch0_0.h264" \
"/video1" \
"/media/video1"
do

timeout 5 ffprobe \
-loglevel error \
-rtsp_transport tcp \
"rtsp://$IP:554$PATH" \
> "$HOME/rtsp.tmp" 2>&1

if grep -qi "Video" "$HOME/rtsp.tmp"; then
echo "[RTSP FOUND]"
echo "rtsp://$IP:554$PATH"
fi

done
fi

done

echo
echo "========================================="
echo " FINISHED "
echo "========================================="
EOF

chmod +x camscan.sh && ./camscan.sh
