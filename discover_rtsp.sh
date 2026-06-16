#!/data/data/com.termux/files/usr/bin/bash

IPS="
192.168.100.18
192.168.100.20
"

PATHS="
/
/11
/12
/1
/live
/live/ch0
/live/ch00_0
/live/ch00_1
/ch0.h264
/ch0_0.h264
/h264
/media/video1
/video1
"

for IP in $IPS
do
 echo
 echo "===== $IP ====="

 for P in $PATHS
 do

  echo "[*] $P"

  timeout 6 ffprobe \
    -loglevel error \
    "rtsp://$IP:554$P" \
    >/tmp/rtsp.out 2>&1

  if grep -qi "Video" /tmp/rtsp.out
  then
      echo "[FOUND] $IP $P"
  fi

 done

done
