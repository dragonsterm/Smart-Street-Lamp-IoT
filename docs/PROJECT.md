# Konteks Smart Street Lamp

Satu ESP32 mengendalikan tiga lampu secara lokal. Sensor LDR membaca cahaya relatif, HC-SR04 mengukur jarak objek di awal ruas, dan INA219 mengukur tegangan, arus, dan daya pada cabang ketiga lampu. Dashboard menampilkan nilai yang dilaporkan perangkat beserta waktu pembaruan terakhir.

Saat gelap dan jalan sepi, profil adaptif mempertahankan ketiga lampu pada 100% selama 10 detik, lalu meredupkan lampu secara bertahap ke 50% selama 10 detik berikutnya. Kendaraan yang terdeteksi mengembalikan ketiga lampu ke 100%. Mode manual mengatur PWM setiap lampu secara terpisah.

## Aliran data

1. ESP32 meminta ID sesi melalui `POST /api/devices/{id}/boot` dan membuka `/api/devices/{id}/live` untuk mengirim telemetri dan menerima perintah.
2. FastAPI memvalidasi token serta nomor urut, lalu menyimpan telemetri dan status perintah di MySQL. Dashboard membuka `/api/live` untuk menerima pembaruan dan mengambil riwayat dari `/api/history`.
3. Dashboard mengirim pengaturan melalui `POST /api/commands`. Perintah berlaku selama 30 detik. Dashboard menampilkan status diterapkan setelah menerima konfirmasi yang cocok.

Dashboard menandai perangkat luring setelah 15 detik tanpa telemetri baru. Setelah 30 detik tanpa kontak server, firmware kembali ke mode otomatis adaptif.

## Referensi

- *Perancangan Sistem Smart Street Lamp Berbasis IoT* edisi revisi, terutama bagian Rancangan Arsitektur, Lapisan Middleware, Lapisan Aplikasi, dan Proses Kerja Sistem.
- [Firmware fisik](../proyek.cpp) memuat konfigurasi pin, aturan kendali, dan format pesan perangkat yang digunakan. Diagram rangkaian awal mencatat rancangan simulasi.
- [Diagram rangkaian awal](../diagram.json) dan [diagram sistem](diagrams/README.md).
- [Dokumentasi WebSocket FastAPI](https://fastapi.tiangolo.com/advanced/websockets/) dan [dokumentasi INA219 Texas Instruments](https://www.ti.com/product/INA219).
