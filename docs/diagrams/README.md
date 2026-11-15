# Diagram Smart Street Lamp

Diagram disimpan sebagai SVG mandiri dengan font dan bentuk beratribut eksplisit, tanpa CSS atau dependensi daring. Buka langsung dari file atau browser. Tidak ditampilkan dan tidak dipublikasikan oleh dashboard.

- `01-dfd-context.svg`: batas layanan dan aliran dari pengguna / ESP32.
- `02-dfd-level1.svg`: proses Gane–Sarson dan penyimpanan data.
- `03-control-flowchart.svg`: flowchart keputusan ketiga lampu dan histeresis.
- `04-database-erd.svg`: tabel MySQL aktual, tipe field, indeks dan relasi logis crow’s foot.
- `05-data-stream.svg`: sequence HTTP, JSON, UUID, expiry, telemetri dan ACK.

Relasi ERD bersifat logis dan diperiksa aplikasi; skema SQL tidak mendefinisikan FOREIGN KEY. D0 pada DFD adalah konfigurasi lokal, bukan tabel akun. Tidak ada gambar sambungan LDR / ultrasonik di folder ini; rangkaian yang dapat dijalankan berada di `diagram.json` pada root repo.
