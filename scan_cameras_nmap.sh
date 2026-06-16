#!/bin/bash

SUBNET="192.168.100.0/24"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
OUTPUT="camaras_nmap_$TIMESTAMP"

echo "[+] Escaneando cámaras IP en $SUBNET ..."
echo "[+] Esto puede tardar entre 20-60 segundos."

# Escaneo agresivo pero eficiente
nmap -sS -Pn -p 80,443,554,8000,8080,37777 --open \
     --min-rate 1000 \
     -oN "$OUTPUT.txt" \
     -oG "$OUTPUT.grep" \
     $SUBNET

echo ""
echo "[+] Escaneo completado."
echo "[+] Resultados guardados en:"
echo "    - $OUTPUT.txt (formato normal)"
echo "    - $OUTPUT.grep (formato grepable)"

# Mostrar resumen
echo ""
echo "[+] Posibles cámaras encontradas:"
grep "open" "$OUTPUT.grep" | awk '{print $2, $NF}'
