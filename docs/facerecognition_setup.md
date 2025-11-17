# Face Recognition Preparation

1. **Install Python dependencies**  
   - Local dev: `source venv/bin/activate && pip install -r requirements.txt`.  
   - Docker: rebuild the backend image so `numpy` dan `opencv-python-headless` ikut ter-install.

2. **Ensure OS libraries**  
   Dockerfile kini meng-install `libgl1`, `libglib2.0-0`, `libsm6`, `libxext6`, dan `libxrender1`. Jika menjalankan langsung di host, pasang paket serupa melalui manajer paket distro Anda.

3. **Model dan aset**  
   - Pastikan `hasiltraining/haarcascade_frontalface_default.xml` tersedia.  
   - Direktori `hasiltraining/` dan `media/imagetraining/` harus writable oleh proses Django.  
   - Endpoint training otomatis membuat/menimpa `hasiltraining/lbph_model.xml`.

4. **Workflow API**  
   - Latih data owner: `POST /api/v1/facerecognition/createimagetrainingusernew/` (`username`, `image_list[]`).  
   - Verifikasi data: `GET /api/v1/facerecognition/getuserimageexists/?username=...`.  
   - Kirim hasil kamera: `POST /api/v1/facerecognition/createlogusersmartnew/` dengan `image`.  
   - Ambil log: `GET /api/v1/facerecognition/getuserlogsmartnew/`.

5. **Monitoring**  
   Jalankan `python manage.py check` atau `docker compose logs backend` setelah rebuild untuk memastikan dependensi baru termuat dan tidak ada ImportError.
