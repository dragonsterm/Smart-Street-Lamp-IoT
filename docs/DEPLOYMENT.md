# Konfigurasi dan deploy

## Database dan kredensial

`.env.example` mencantumkan variabel lingkungan. Simpan `.env` yang sudah dipakai perangkat. Jika akun MySQL sudah ada, isi `MYSQL_PASSWORD` dengan password akun tersebut. Skrip `setup_mysql.py` memakai admin MySQL `root` secara default dan membaca `MYSQL_ADMIN_PASSWORD` dari lingkungan apabila diperlukan. Jika database jarak jauh menyediakan berkas CA, atur `MYSQL_SSL_CA` ke path berkas itu pada host aplikasi.

## Perangkat fisik

Firmware yang sudah terpasang membuka HTTP dan WebSocket ke alamat LAN yang dikonfigurasi pada perangkat, dengan port 8000. Gunakan `--host 0.0.0.0 --port 8000` pada host LAN tersebut saat perangkat perlu terhubung. Pengujian website di desktop memakai `127.0.0.1:8000`. `DEVICE_ID` dan `DEVICE_TOKEN` dari server harus cocok dengan firmware.

Isi `include/device_secrets.h` secara lokal dengan menyalin [contoh konfigurasi](../include/device_secrets.example.h). Git mengabaikan header yang berisi kredensial. Untuk membangun [proyek.cpp](../proyek.cpp) di luar proses deploy website, gunakan [konfigurasi PlatformIO](../platformio.ini) dari direktori repo.

```powershell
pio run --environment esp32dev
```

## Pengujian API

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe scripts/setup_mysql.py --include-test
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Tes menggunakan database terpisah dengan nama berakhiran `_test`.

## Hosting gratis

**Target biaya hosting Rp0.** Untuk instalasi yang sudah teruji, jalankan FastAPI dan MySQL XAMPP di komputer sendiri. Lihat langkahnya di [README](../README.md).

### Render Free

Hubungkan repo sebagai [Blueprint](https://render.com/docs/blueprint-spec). [render.yaml](../render.yaml) memilih `plan: free`, deploy otomatis setelah commit, dan pemeriksaan `/health`. Gunakan subdomain `onrender.com` dan workspace tanpa metode pembayaran. Jika kuota bulanan habis, Render dapat menangguhkan layanan atau menghentikan build baru. Layanan juga tidur setelah 15 menit tanpa lalu lintas masuk. [Batas Render Free](https://render.com/docs/free)

Render Free menyediakan hosting aplikasi. Proyek ini memerlukan database MySQL/MariaDB gratis yang dapat dijangkau dari Render. MySQL XAMPP pada `127.0.0.1` hanya dapat dijangkau dari komputer tempat XAMPP berjalan. Selama belum ada database gratis yang kompatibel dan dapat dijangkau, gunakan instalasi lokal. Isi `MYSQL_HOST`, `MYSQL_USER`, `MYSQL_PASSWORD`, dan `MYSQL_DATABASE` saat memasang Blueprint. Atur `MYSQL_PORT` jika penyedia menggunakan port selain 3306. Akun aplikasi membutuhkan izin `SELECT, INSERT, UPDATE, DELETE, CREATE, INDEX` pada database.

### VPS

Gunakan mesin yang sudah tersedia atau VPS dengan kuota gratis. Pasang Python 3.11 serta MySQL/MariaDB. Jalankan `python3.11 -m venv .venv` dan `.venv/bin/python -m pip install -r requirements.txt`. Setel service manager untuk menjalankan `.venv/bin/python -m uvicorn server.app:create_app --factory --host 127.0.0.1 --port 8000 --workers 1`. Untuk akses publik, tempatkan reverse proxy HTTPS di depan Uvicorn, teruskan WebSocket serta header `Host` dan `X-Forwarded-Proto`, lalu setel `COOKIE_SECURE=true`.

### Vercel Hobby

Backend saat ini memakai antrean dalam satu proses FastAPI. Hosting backend lewat Vercel Functions memerlukan antrean bersama dan penanganan batas durasi WebSocket, sehingga konfigurasi deploy gratis untuk versi ini memakai Render Free atau instalasi lokal. [Dokumentasi WebSocket Vercel](https://vercel.com/i/wsgi-vs-asgi)

Perangkat yang sudah diprogram tetap menghubungi alamat LAN yang tersimpan pada firmware.
