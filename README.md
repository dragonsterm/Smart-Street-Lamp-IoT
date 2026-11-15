# Smart Street Lamp

Dashboard FastAPI untuk memantau ESP32 dan mengendalikan tiga lampu jalan. Telemetri dan riwayat perintah tersimpan di MySQL/MariaDB.

## Jalankan di Windows

Nyalakan **MySQL** di XAMPP Control Panel. Gunakan Python 3.11 dan jalankan perintah ini dari direktori repo.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts/configure.py
.\.venv\Scripts\python.exe scripts/setup_mysql.py
.\.venv\Scripts\python.exe -m uvicorn server.app:create_app --factory --host 127.0.0.1 --port 8000
```

Buka [dashboard](http://127.0.0.1:8000/) dan masuk dengan akun di `.env`. [Health check](http://127.0.0.1:8000/health) memeriksa koneksi database. Untuk instalasi yang sudah berfungsi, gunakan `.env` yang sama. Buat ulang `.venv` setiap kali pindah komputer.

- [Konteks proyek dan referensi](docs/PROJECT.md)
- [Konfigurasi perangkat, pengujian, dan deploy](docs/DEPLOYMENT.md)
- [Firmware fisik](proyek.cpp) dan [diagram rangkaian](diagram.json)
- [Lisensi MIT](LICENSE)
