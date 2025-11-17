import os
from datetime import datetime

from django.shortcuts import render
from PIL import Image
from django.core.files.base import ContentFile
from rest_framework import status
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.users.models import User

from . import models, serializer

try:
    import numpy as np
    import cv2
except ModuleNotFoundError:
    np = None
    cv2 = None


def _dependencies_available():
    return np is not None and cv2 is not None


def _dependency_missing_response():
    return Response(
        data={
            'status': 'error',
            'message': 'Face recognition dependencies (numpy, opencv-python) are not installed on the server.',
        },
        status=status.HTTP_503_SERVICE_UNAVAILABLE,
    )
# Create your views here.
def get_trained_images(log_file, user_folder):
    """Membaca daftar gambar yang sudah dilatih dari file log."""
    trained_images = set()
    if os.path.exists(log_file):
        with open(log_file, "r") as file:
            trained_images = set(file.read().splitlines())
    return trained_images

def update_trained_images(log_file, new_images):
    """Menambahkan gambar baru yang telah dilatih ke file log."""
    with open(log_file, "a") as file:
        for image in new_images:
            file.write(f"{image}\n")
def update_trained_images2(log_file, new_images):
    """
    Menulis ulang log file dengan gambar baru yang telah dilatih.
    """
    with open(log_file, "w") as file:  # Gunakan mode "w" untuk menimpa log lama
        for image in new_images:
            file.write(f"{image}\n")

def non_max_suppression_fast(boxes, overlapThresh=0.3):
    if len(boxes) == 0:
        return []

    boxes = np.array(boxes)
    if boxes.dtype.kind == "i":
        boxes = boxes.astype("float")

    pick = []

    x1 = boxes[:,0]
    y1 = boxes[:,1]
    x2 = boxes[:,0] + boxes[:,2]
    y2 = boxes[:,1] + boxes[:,3]

    area = (x2 - x1 + 1) * (y2 - y1 + 1)
    idxs = np.argsort(y2)  # bisa juga gunakan area

    while len(idxs) > 0:
        last = idxs[-1]
        pick.append(last)

        xx1 = np.maximum(x1[last], x1[idxs[:-1]])
        yy1 = np.maximum(y1[last], y1[idxs[:-1]])
        xx2 = np.minimum(x2[last], x2[idxs[:-1]])
        yy2 = np.minimum(y2[last], y2[idxs[:-1]])

        w = np.maximum(0, xx2 - xx1 + 1)
        h = np.maximum(0, yy2 - yy1 + 1)
        overlap = (w * h) / area[idxs[:-1]]

        idxs = np.delete(idxs, np.concatenate(([len(idxs)-1], np.where(overlap > overlapThresh)[0])))

    return boxes[pick].astype("int")
def train_or_update_user_data(training_dir, model_save_path, target_user, target_label):
    print(target_user)
    print(target_label)
    face_recognizer = cv2.face.LBPHFaceRecognizer.create()
    haar_name='haarcascade_frontalface_default.xml'
    haar_loc=os.path.join('hasiltraining',haar_name)
    face_cascade = cv2.CascadeClassifier(haar_loc)

    log_file = f"{target_user}_trained_images.log"
    trained_images = get_trained_images(log_file, target_user)
    faces = []
    labels = []

    user_path = os.path.join(training_dir, target_user)
    new_images = []

    # Pastikan folder user ada
    if os.path.isdir(user_path):
        for file in os.listdir(user_path):
            if file.endswith(("jpg", "jpeg", "png")):
                image_path = os.path.join(user_path, file)
                if image_path in trained_images:
                    continue  # Skip image yang sudah dilatih sebelumnya

                image = Image.open(image_path).convert("L")
                image_np = np.array(image, "uint8")
                detected_faces = face_cascade.detectMultiScale(image_np, 1.2, 5)
                for (x, y, w, h) in detected_faces:
                    faces.append(image_np[y:y+h, x:x+w])
                    labels.append(target_label)
                    print(labels)
                    print(f"Detected faces in {file}: {detected_faces}")
                    new_images.append(image_path)

        print(f"Data wajah ditemukan untuk user {target_user}: {len(faces)} gambar baru.")
    else:
        print(f"Folder user {target_user} tidak ditemukan.")

    if faces:
        # Update model dengan data baru
        if os.path.exists(model_save_path):
            face_recognizer.read(model_save_path)

        face_recognizer.update(faces, np.array(labels))
        face_recognizer.save(model_save_path)
        print(np.array(labels))
        print(f"Data baru untuk user {target_user} ditambahkan ke model dan disimpan di {model_save_path}.")

        # Update daftar gambar yang telah dilatih
        update_trained_images(log_file, new_images)
    else:
        print(f"Tidak ada wajah baru untuk user {target_user}.")

def train_replace_user_data(training_dir, model_save_path, target_user, target_label):
    """
    Mengganti semua data wajah user yang ada di log dan menggantinya dengan gambar baru dari folder user.
    """
    face_recognizer = cv2.face.LBPHFaceRecognizer.create()
    haar_name='haarcascade_frontalface_default.xml'
    haar_loc=os.path.join('hasiltraining',haar_name)
    face_cascade = cv2.CascadeClassifier(haar_loc)

    log_file = f"{target_user}_trained_images.log"
    faces = []
    labels = []

    user_path = os.path.join(training_dir, target_user)
    new_images = []

    # Pastikan folder user ada
    if os.path.isdir(user_path):
        # Hapus semua entri lama di log
        if os.path.exists(log_file):
            os.remove(log_file)
            print(f"Log file lama untuk {target_user} dihapus.")

        # Proses semua gambar di folder user
        for file in os.listdir(user_path):
            if file.endswith(("jpg", "jpeg", "png")):
                image_path = os.path.join(user_path, file)
                image = Image.open(image_path).convert("L")
                image_np = np.array(image, "uint8")
                detected_faces = face_cascade.detectMultiScale(image_np, 1.2, 5)
                for (x, y, w, h) in detected_faces:
                    faces.append(image_np[y:y+h, x:x+w])
                    labels.append(target_label)
                    new_images.append(image_path)

        print(f"Data wajah ditemukan untuk user {target_user}: {len(faces)} gambar.")
    else:
        print(f"Folder user {target_user} tidak ditemukan.")
        return  # Tidak ada folder, tidak ada pelatihan

    if faces:
        # Latih ulang model dari awal atau update model
        if os.path.exists(model_save_path):
            face_recognizer.read(model_save_path)
        else:
            print("Model baru akan dibuat.")

        face_recognizer.train(faces, np.array(labels))
        face_recognizer.save(model_save_path)
        print(f"Model untuk user {target_user} disimpan di {model_save_path}.")

        # Update log file dengan gambar baru
        update_trained_images2(log_file, new_images)
    else:
        print(f"Tidak ada data wajah yang valid untuk user {target_user}.")

def clear_log_for_user(log_file):
    """Hapus semua referensi log untuk user tertentu."""
    if os.path.exists(log_file):
        os.remove(log_file)

def recognize_from_image(image, model_path, label_to_user):
    """
    Melakukan proses pengenalan wajah pada gambar yang diberikan.
    """
    # Load the trained model
    recognizer = cv2.face.LBPHFaceRecognizer.create()
    recognizer.read(model_path)

    # Load the face detection model
    haar_name='haarcascade_frontalface_default.xml'
    haar_loc=os.path.join('hasiltraining',haar_name)
    face_cascade = cv2.CascadeClassifier(haar_loc)

    # Konversi gambar ke grayscale
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    # Deteksi wajah pada gambar
    raw_faces = face_cascade.detectMultiScale(gray, scaleFactor=1.2, minNeighbors=5, minSize=(50, 50))
    faces=non_max_suppression_fast(raw_faces,overlapThresh=0.3)
    results = []
    for (x, y, w, h) in faces:
        face_image = gray[y:y+h, x:x+w]
        try:
            label, confidence = recognizer.predict(face_image)
            print(label)
            print(confidence)
        except Exception as e:
            # Jika error saat prediksi
            print(f"Error predicting face: {e}")
            continue
        if confidence >= 50:
            username = 'Unknown'
            id_user=None
            status = "Unauthorized"
        else:
            username = label_to_user.get(label, "Unknown")
            id_user=label
            status = "Authorized"
        print(id_user)
        print(username)
        color= (0, 255, 0) if confidence < 50 else (0, 0, 255)
        cv2.putText(image, username, (x+100,y-10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
        cv2.rectangle(image, (x, y), (x + w, y + h), (255, 0, 0), 2)
        results.append({
            "id_face_user": id_user,
            "username":username,
            "status": status,
            "confidence":"  {0}%".format(round(100 - confidence)),
            "face_image": image
        })
    print('result from def recognize image',results)
    return results

def get_true_label_from_path(image_path):
    # Assuming the label is the first part of the filename, split by "_"
    label_str = os.path.basename(image_path).split("_")[0]
    return str(label_str)

class Createimagetrainingusernew(APIView):
    parser_classes = [MultiPartParser,FormParser]
    def post(self,request):
        if not _dependencies_available():
            return _dependency_missing_response()
        username=request.data.get("username",None)
        images=request.FILES.getlist("image_list")
        if not username:
            return Response({
                "error":"username has invalid"
            },status=status.HTTP_400_BAD_REQUEST)
        if not images:
            return Response({"error": "No images found."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            items=User.objects.get(username=username)
        except User.DoesNotExist:
            return Response({
                "error":"username does not exist"
            },status=status.HTTP_403_FORBIDDEN)

        savedimage=[]
        for image in images:
            serial=serializer.Imagedatawajahserializernew(data={
                "user":items.id,
                "image_user":image
            })
            if serial.is_valid():
                serial.save()
                training_dir=os.path.join('media','imagetraining')
                model_save='lbph_model.xml'
                model_save_path=os.path.join('hasiltraining',model_save)
                train_or_update_user_data(
                    training_dir=training_dir,
                    model_save_path=model_save_path,
                    target_user=items.first_name + items.last_name,
                    target_label=int(items.face_id)
                )
                savedimage.append(serial.data)
            else:
                return Response(
                    data={
                        "status":"error",
                        "message":serial.errors
                    },status=status.HTTP_400_BAD_REQUEST
                )
        return Response(
            data={
                'status':'success',
                'message':'berhasil mendaftar gambar wajah untuk user baru',
                'data':savedimage
            },status=status.HTTP_200_OK
        )


class Getimageexistsuser(APIView):
    def get(self,request):
        username=request.query_params.get("username",None)
        try:
            items=User.objects.get(username=username)
        except User.DoesNotExist:
            return Response({
                "error":"username does not exist"
            },status=status.HTTP_403_FORBIDDEN)
        gambarexist=models.Datawajahnew.objects.filter(user_id=items.id)
        print(gambarexist)
        if not gambarexist:
            return Response({
                "error":"gambar user tidak ditemukan"
            },status=status.HTTP_403_FORBIDDEN)
        else:
            data=serializer.Imagedatawajahserializernew(gambarexist,many=True)
            return Response(
                data={
                    'status':'success',
                    'message':'gambar user ditemukan',
                    'data':data.data
                },status=status.HTTP_200_OK
            )

class Createlogusersmartnew(APIView):
    parser_classes = [MultiPartParser,FormParser]
    def post(self,request):
        if not _dependencies_available():
            return _dependency_missing_response()
        image_file=request.FILES.get('image')
        print(image_file)
        if not image_file:
            return Response(data={
                'status':"error",
                "message":"Selain gambar tidak diperbolehkan"
            },status=status.HTTP_400_BAD_REQUEST)
        image_byte=image_file.read()
        np_image=np.frombuffer(image_byte,dtype=np.uint8)
        image=cv2.imdecode(np_image,cv2.IMREAD_COLOR)
        label_to_user = {
            int(items.face_id): f"{items.first_name}_{items.last_name}"
            for items in User.objects.all()
            if items.face_id is not None and str(items.face_id).isdigit()
        }
        model_name='lbph_model.xml'
        model_path=os.path.join('hasiltraining',model_name)
        result=recognize_from_image(image,model_path,label_to_user)
        image_result=[]
        for results in result:
            if not results['id_face_user']:
                waktu=datetime.now().strftime('%Y%m%d_%H%M%S_%f')
                _,imagebuffer=cv2.imencode('.jpeg',results['face_image'])
                file_name = f"Unknown_{waktu}.jpeg"
                log_serial=serializer.Logsmartaccesserializernew(data={
                    'id_face_user':None,
                    'image':ContentFile(imagebuffer.tobytes(),name=file_name),
                    'status':results['status']
                })
                if log_serial.is_valid():
                    log_serial.save()
                    image_result.append(log_serial.data)
                else:
                    print(f"Error saving log for unknown user: {log_serial.errors}")
                    return Response(data={
                        "status":"error",
                        "message":log_serial.errors
                    }, status=status.HTTP_400_BAD_REQUEST)
                return Response(data={
                    "result":image_result,
                    "confidence":results['confidence']
                })
            _, image_buffer = cv2.imencode(".jpeg", results['face_image'])
            waktu=datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            file_name = f"{results['username']}_{results['status']}_{waktu}.jpeg"
            log_serial=serializer.Logsmartaccesserializernew(data={
                'id_face_user':results['id_face_user'],
                'image':ContentFile(image_buffer.tobytes(),name=file_name),
                'status':results['status']
            })
            if log_serial.is_valid():
                log_serial.save()
                image_result.append(log_serial.data)
            else:
                print(f"Error saving log for unknown user: {log_serial.errors}")
                return Response(data={
                    "status":"error",
                    "message":log_serial.errors
                }, status=status.HTTP_400_BAD_REQUEST)
            return Response(
                data={
                    "result":image_result,
                    "confidence":results['confidence']
                },status=status.HTTP_200_OK
            )
class Getuserlogsmartnews(APIView):
    def get(self,request):
        username=request.query_params.get('username',None)
        items=User.objects.get(username=username)
        if items.username is None:
            return Response(data={
                'status':"error",
                'message':'username tidak ditemukan'
            },status=status.HTTP_400_BAD_REQUEST)
        finduserimage=models.Logsmartaccess2.objects.filter(id_face_user=items.face_id)
        if finduserimage.exists() is False:
            return Response(data={
                'status':'error',
                'mesage':'user belum terdaftar'
            },status=status.HTTP_400_BAD_REQUEST)
        serial=serializer.Logsmartaccesserializernew(finduserimage,many=True)
        return Response(
            data={
                'status':'success',
                'data':serial.data
            },status=status.HTTP_200_OK
        )
