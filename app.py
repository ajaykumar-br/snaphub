import os
import json
import requests
import pyrebase
import google.generativeai as genai
from google.cloud import storage
from flask import Flask, redirect, request, render_template, send_from_directory, session, url_for

current_directory = os.getcwd()
filePath = os.path.join(current_directory, "snaphub-keys.json")

with open(filePath) as config_file:
    config = json.load(config_file)
    print(config)

app = Flask(__name__)
app.secret_key = config["app_secret"]

os.makedirs('files', exist_ok = True)
bucket_name = 'snaphubimages'
storage_client = storage.Client()
bucket = storage_client.bucket(bucket_name)

# configurations
genai.configure(api_key=config["genai_secret"])

# firebase
firebaseConfig = {
  "apiKey": config["firebase_secret"],
  "authDomain": "balmy-sanctuary-436201-r2.firebaseapp.com",
  "databaseURL": "https://balmy-sanctuary-436201-r2-default-rtdb.firebaseio.com",
  "projectId": "balmy-sanctuary-436201-r2",
  "storageBucket": "balmy-sanctuary-436201-r2.appspot.com",
  "messagingSenderId": "529741925165",
  "appId": "1:529741925165:web:4a1bd0013a031381fda1a4"
};

# gemini 
generation_config = {
  "temperature": 1,
  "top_p": 0.95,
  "top_k": 64,
  "max_output_tokens": 8192,
  "response_mime_type": "text/plain",
}

# authorize firebase
firebase = pyrebase.initialize_app(firebaseConfig)
auth = firebase.auth()

def upload_blob(bucket_name, file, destination_blob_name, user_id):
    blob = bucket.blob(f"{user_id}/{destination_blob_name}")
    blob.upload_from_file(file)

def download_blob(bucket_name, source_file, destination_file):
    os.makedirs(os.path.dirname(destination_file), exist_ok=True)

    blob = bucket.blob(source_file)
    blob.download_to_filename(destination_file)

def list_blobs(bucket_name, user_id):
    blobs = bucket.list_blobs(prefix=f"{user_id}/")
    return [blob.name for blob in blobs]

@app.route('/')
def index():
    userId = session.get('user')
    if not userId:
        return redirect('/login')

    user_data_folder = os.path.join('files', userId)
    os.makedirs(user_data_folder, exist_ok=True)

    user_blob_names = list_blobs(bucket_name, userId)

    for names in user_blob_names:
        local_path = os.path.join(user_data_folder, names.split('/')[-1])
        if not os.path.exists(local_path):
            download_blob(bucket_name, names, local_path)

    user_local_files = os.listdir(user_data_folder)
    for user_file in user_local_files:
        user_file_path = os.path.join(user_data_folder, user_file)
        if os.path.isfile(user_file_path):
            if user_file not in [blob.split('/')[-1] for blob in user_blob_names]:
                os.remove(user_file_path)

    file_list = {}
    for file in user_blob_names:
        if file.lower().endswith(('.jpg', '.jpeg', '.png')):
            textfile = os.path.splitext(file)[0] + '.txt'
            description = None

            if os.path.exists(os.path.join(user_data_folder, textfile)):
                with open(os.path.join(user_data_folder, textfile), 'r') as tf:
                    description = tf.read()
                
            if os.path.exists(os.path.join(user_data_folder, os.path.basename(file))):
                file_list[os.path.basename(file)] = description

    return render_template('index.html', files=file_list, user_id=userId)

# given by aistudio
def upload_to_gemini(path, mime_type=None):
    file = genai.upload_file(path, mime_type=mime_type)
    print(f"Uploaded file '{file.display_name}' as: {file.uri}")
    return file

def generativeAI(save_file):
    model = genai.GenerativeModel(
    model_name="gemini-1.5-flash",
    generation_config=generation_config,
    )

    file1 = upload_to_gemini(save_file, mime_type="image/jpeg")

    chat_session = model.start_chat(
    history=[
        {
        "role": "user",
        "parts": [
            file1,
            "Generate the title and description for the below image and return the response in json format",
        ],
        }
    ]
    )
    response = chat_session.send_message("INSERT_INPUT_HERE")
    return response.text

@app.route('/upload', methods=['POST'])
def upload():
    if 'user' not in session:
        return redirect('/login')

    user_id = session['user']
    user_folder = os.path.join('files', user_id)

    os.makedirs(user_folder, exist_ok=True)

    file = request.files['form_file']
    filename = file.filename

    local_path = os.path.join(user_folder, filename)
    file.save(local_path)

    response = generativeAI(local_path)
    
    try:
        response = response.replace('json', "").replace("```", "").strip()
        res = json.loads(response)
        title = res.get("title", "No Title Available")
        description = res.get("description", "No Title Available")
    except:
        print("Error decoding JSON response.")
        return "Error generating response"

    textfile_path = os.path.join(user_folder, os.path.splitext(filename)[0] + '.txt')
    with open(textfile_path, 'w') as tf:
        tf.write(f"{title}\n{description}")

    with open(textfile_path, 'rb') as tf:
        upload_blob(bucket_name, tf, os.path.basename(textfile_path), user_id)

    file.seek(0)
    upload_blob(bucket_name, file, os.path.basename(local_path), user_id)

    return redirect('/')

@app.route('/files/<user_id>/<filename>')
def get_file(filename, user_id):
    files = send_from_directory(os.path.join('files', user_id), filename)
    return files

def parse_title_description(content):
    lines = content.split('\n')
    title = lines[0].strip() if lines else "No Title Available"
    description = '\n'.join(lines[1:]).strip() if len(lines)>1 else "No Description Available"
    return title, description

@app.route('/view/<user_id>/<filename>')
def view_file(user_id, filename):
    textfile = os.path.splitext(filename)[0] + '.txt'
    title = "No Title Available"
    description = "No Description Available"

    textfile_path = os.path.join('./files', user_id, textfile)
    if os.path.exists(textfile_path):
        with open(textfile_path, 'r') as tf:
            content = tf.read()
            title, description = parse_title_description(content)

    return render_template('imageView.html', filename=filename, title=title, description=description, user_id=user_id)

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        email = request.form["email"]
        password = request.form["password"]
        try:
            user = auth.create_user_with_email_and_password(email, password)
            session['user'] = user['localId']
            return redirect('/')
        except Exception as e:
            return f"Error: {str(e)}"
    
    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        try:
            user = auth.sign_in_with_email_and_password(email, password)
            session['user'] = user['localId']
            return redirect('/')
        except:
            return "Invalid login credentials"
    return render_template('login.html')

@app.route('/logout', methods=['POST'])
def logout():
    session.pop('user', None)
    return redirect('/login')

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=True)