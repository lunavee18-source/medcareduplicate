from flask import Flask, jsonify, request, session, render_template, send_from_directory
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date, timedelta
from dotenv import load_dotenv
from groq import Groq
import os, json, secrets

load_dotenv()
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///medcare.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SESSION_PERMANENT'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)

CORS(app, supports_credentials=True)

db = SQLAlchemy(app)

client = None
else:
    print("GROQ KEY MISSING — fallback mode")

@app.before_request
def make_session_permanent():
    session.permanent = True

@app.route("/")
def home():
    return send_from_directory('static', 'index.html')

@app.route("/manifest.json")
def manifest():
    return send_from_directory('static', 'manifest.json')

@app.route("/sw.js")
def service_worker():
    return send_from_directory('static', 'sw.js')

@app.route("/icons/<path:filename>")
def icons(filename):
    return send_from_directory('static/icons', filename)



# ─────────────── MODELS ───────────────

class User(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    name         = db.Column(db.String(120))
    email        = db.Column(db.String(120), unique=True, nullable=False)
    password     = db.Column(db.String(256), nullable=False)
    role         = db.Column(db.String(20), default='user')
    age          = db.Column(db.Integer, default=25)
    gender       = db.Column(db.String(20))
    phone        = db.Column(db.String(20))
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)

class Hospital(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    user_id      = db.Column(db.Integer)
    name         = db.Column(db.String(200), nullable=False)
    reg_number   = db.Column(db.String(100), default='')
    address      = db.Column(db.String(300), default='')
    city         = db.Column(db.String(100), default='Tumkur')
    state        = db.Column(db.String(100), default='Karnataka')
    pincode      = db.Column(db.String(20), default='')
    phone        = db.Column(db.String(30), default='')
    email        = db.Column(db.String(120), default='')
    h_type       = db.Column(db.String(50), default='Private')
    beds         = db.Column(db.Integer, default=100)
    established  = db.Column(db.Integer, default=2000)
    description  = db.Column(db.Text, default='')
    services     = db.Column(db.Text, default='[]')
    doctors_json = db.Column(db.Text, default='[]')

class HealthLog(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    user_id      = db.Column(db.Integer)
    log_date     = db.Column(db.String(20))
    cal_consumed = db.Column(db.Integer, default=0)
    cal_goal     = db.Column(db.Integer, default=2000)
    protein      = db.Column(db.Float, default=0)
    carbs        = db.Column(db.Float, default=0)
    fat          = db.Column(db.Float, default=0)
    food_log     = db.Column(db.Text, default='[]')
    exercises    = db.Column(db.Text, default='[]')

class Appointment(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    user_id      = db.Column(db.Integer)
    hospital_id  = db.Column(db.Integer)
    doctor_name  = db.Column(db.String(120))
    doctor_spec  = db.Column(db.String(100))
    slot_date    = db.Column(db.String(30))
    slot_time    = db.Column(db.String(20))
    fee          = db.Column(db.Integer, default=0)
    notes        = db.Column(db.Text, default='')
    reminder_at  = db.Column(db.String(50), default='')
    status       = db.Column(db.String(20), default='pending')
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)

class Reminder(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    user_id      = db.Column(db.Integer)
    name         = db.Column(db.String(200))
    time_str     = db.Column(db.String(10))
    frequency    = db.Column(db.String(30), default='Daily')
    active       = db.Column(db.Boolean, default=True)

with app.app_context():
    db.create_all()

# ─────────────── HELPERS ───────────────

def get_or_create_log(uid):
    today = date.today().isoformat()
    log = HealthLog.query.filter_by(user_id=uid, log_date=today).first()
    if not log:
        log = HealthLog(user_id=uid, log_date=today)
        db.session.add(log)
        db.session.commit()
    return log

def user_dict(u):
    return {'id':u.id,'name':u.name,'email':u.email,'role':u.role,'age':u.age,'gender':u.gender,'phone':u.phone}

def hospital_dict(h):
    doctors = json.loads(h.doctors_json or '[]')
    for d in doctors:
        if 'specialization' in d and 'specialty' not in d:
            d['specialty'] = d['specialization']
        if 'specialty' in d and 'specialization' not in d:
            d['specialization'] = d['specialty']
    return {
        'id':h.id,'name':h.name,'reg_number':h.reg_number,
        'address':h.address,'city':h.city,'state':h.state,'pincode':h.pincode,
        'phone':h.phone,'email':h.email,'h_type':h.h_type,'beds':h.beds,
        'established':h.established,'description':h.description,
        'services':json.loads(h.services or '[]'),
        'doctors':doctors
    }

def appt_dict(a):
    hosp = Hospital.query.get(a.hospital_id)
    user = User.query.get(a.user_id)
    return {
        'id':a.id,'user_id':a.user_id,'hospital_id':a.hospital_id,
        'user_name': user.name if user else 'Unknown',
        'hospital_name': hosp.name if hosp else 'Unknown',
        'doctor_name':a.doctor_name,'doctor_spec':a.doctor_spec,
        'slot_date':a.slot_date,'slot_time':a.slot_time,
        'fee':a.fee,'notes':a.notes,'reminder_at':a.reminder_at,
        'status':a.status,'created_at':str(a.created_at)
    }

# ─────────────── AUTH ───────────────

@app.route('/api/signup/user', methods=['POST'])
def signup_user():
    d = request.json or {}
    required = ['name','email','password','age','gender']
    missing = [f for f in required if not str(d.get(f,'')).strip()]
    if missing: return jsonify({'error': f'Missing: {", ".join(missing)}'}), 400
    if User.query.filter_by(email=d['email'].lower()).first():
        return jsonify({'error': 'Email already registered'}), 409
    if len(d['password']) < 6:
        return jsonify({'error': 'Password must be at least 6 characters'}), 400
    u = User(name=d['name'].strip(), email=d['email'].lower().strip(),
             password=generate_password_hash(d['password']),
             role='user', age=int(d['age']), gender=d['gender'], phone=d.get('phone',''))
    db.session.add(u)
    db.session.commit()
    session['user_id'] = u.id
    session['role'] = 'user'
    return jsonify({'success': True, 'user': user_dict(u)})

@app.route('/api/signup/hospital', methods=['POST'])
def signup_hospital():
    d = request.json or {}
    required = ['name','email','password','address','phone','reg_number']
    missing = [f for f in required if not str(d.get(f,'')).strip()]
    if missing: return jsonify({'error': f'Missing: {", ".join(missing)}'}), 400
    if User.query.filter_by(email=d['email'].lower()).first():
        return jsonify({'error': 'Email already registered'}), 409
    if len(d['password']) < 6:
        return jsonify({'error': 'Password must be at least 6 characters'}), 400
    u = User(name=d['name'].strip(), email=d['email'].lower().strip(),
             password=generate_password_hash(d['password']), role='hospital')
    db.session.add(u)
    db.session.flush()
    services = d.get('services', [])
    if isinstance(services, str):
        services = [s.strip() for s in services.split(',') if s.strip()]
    doctors = d.get('doctors', [])
    h = Hospital(
        user_id=u.id, name=d['name'].strip(), reg_number=d['reg_number'],
        address=d['address'], city=d.get('city','Tumkur'), state=d.get('state','Karnataka'),
        pincode=d.get('pincode',''), phone=d['phone'], email=d['email'].lower(),
        h_type=d.get('h_type','Private'), beds=int(d.get('beds',100) or 100),
        established=int(d.get('established', datetime.now().year) or datetime.now().year),
        description=d.get('description',''),
        services=json.dumps(services), doctors_json=json.dumps(doctors)
    )
    db.session.add(h)
    db.session.commit()
    session['user_id'] = u.id
    session['role'] = 'hospital'
    session['hospital_id'] = h.id
    return jsonify({'success': True, 'user': user_dict(u), 'hospital': hospital_dict(h)})

@app.route('/api/login', methods=['POST'])
def login():
    d = request.json or {}
    if not d.get('email') or not d.get('password'):
        return jsonify({'error': 'Email and password required'}), 400
    u = User.query.filter_by(email=d['email'].lower().strip()).first()
    if not u or not check_password_hash(u.password, d['password']):
        return jsonify({'error': 'Invalid email or password'}), 401
    session.clear()
    session.permanent = True
    session['user_id'] = u.id
    session['role'] = u.role
    result = {'success': True, 'user': user_dict(u)}
    if u.role == 'hospital':
        h = Hospital.query.filter_by(user_id=u.id).first()
        if h:
            session['hospital_id'] = h.id
            result['hospital'] = hospital_dict(h)
    return jsonify(result)

@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True})

@app.route('/api/me', methods=['GET'])
def me():
    uid = session.get('user_id')
    if not uid: return jsonify({'error': 'Not logged in'}), 401
    u = User.query.get(uid)
    if not u: return jsonify({'error': 'User not found'}), 404
    result = {'user': user_dict(u)}
    if u.role == 'hospital':
        h = Hospital.query.filter_by(user_id=uid).first()
        if h: result['hospital'] = hospital_dict(h)
    return jsonify(result)

# ─────────────── HEALTH ───────────────

@app.route('/api/health/log', methods=['GET'])
def get_health_log():
    uid = session.get('user_id')
    if not uid: return jsonify({'error': 'Not logged in'}), 401
    log = get_or_create_log(uid)
    return jsonify({
        'cal_consumed': log.cal_consumed, 'cal_goal': log.cal_goal,
        'protein': log.protein, 'carbs': log.carbs, 'fat': log.fat,
        'food_log': json.loads(log.food_log or '[]'),
        'exercises': json.loads(log.exercises or '[]'),
        'log_date': log.log_date,
    })

@app.route('/api/health/food', methods=['POST'])
def log_food():
    uid = session.get('user_id')
    if not uid: return jsonify({'error': 'Not logged in'}), 401
    d = request.json or {}
    log = get_or_create_log(uid)
    food_list = json.loads(log.food_log or '[]')
    item = {
        'name': d.get('name',''), 'cal': int(d.get('cal',0)),
        'protein': float(d.get('protein',0)), 'carbs': float(d.get('carbs',0)),
        'fat': float(d.get('fat',0)), 'id': int(datetime.utcnow().timestamp()*1000)
    }
    food_list.append(item)
    log.food_log = json.dumps(food_list)
    log.cal_consumed = (log.cal_consumed or 0) + item['cal']
    log.protein = (log.protein or 0) + item['protein']
    log.carbs = (log.carbs or 0) + item['carbs']
    log.fat = (log.fat or 0) + item['fat']
    db.session.commit()
    return jsonify({'success': True, 'food_log': food_list, 'cal_consumed': log.cal_consumed})

@app.route('/api/health/food/<int:item_id>', methods=['DELETE'])
def delete_food(item_id):
    uid = session.get('user_id')
    if not uid: return jsonify({'error': 'Not logged in'}), 401
    log = get_or_create_log(uid)
    food_list = json.loads(log.food_log or '[]')
    item = next((f for f in food_list if f.get('id') == item_id), None)
    if item:
        food_list = [f for f in food_list if f.get('id') != item_id]
        log.food_log = json.dumps(food_list)
        log.cal_consumed = max(0, (log.cal_consumed or 0) - item['cal'])
        log.protein = max(0, (log.protein or 0) - item['protein'])
        log.carbs = max(0, (log.carbs or 0) - item['carbs'])
        log.fat = max(0, (log.fat or 0) - item['fat'])
        db.session.commit()
    return jsonify({'success': True})

@app.route('/api/health/exercise', methods=['POST'])
def toggle_exercise():
    uid = session.get('user_id')
    if not uid: return jsonify({'error': 'Not logged in'}), 401
    d = request.json or {}
    ex_id = d.get('exercise_id')
    log = get_or_create_log(uid)
    done = json.loads(log.exercises or '[]')
    if ex_id in done:
        done.remove(ex_id)
    else:
        done.append(ex_id)
    log.exercises = json.dumps(done)
    db.session.commit()
    return jsonify({'exercises': done})

@app.route('/api/health/goal', methods=['POST'])
def set_goal():
    uid = session.get('user_id')
    if not uid: return jsonify({'error': 'Not logged in'}), 401
    d = request.json or {}
    log = get_or_create_log(uid)
    if 'cal_goal' in d: log.cal_goal = int(d['cal_goal'])
    db.session.commit()
    return jsonify({'success': True})

# ─────────────── HOSPITALS ───────────────

@app.route('/api/hospitals', methods=['GET'])
def get_hospitals():
    try:
        hospitals = Hospital.query.all()
        return jsonify([hospital_dict(h) for h in hospitals])
    except Exception as e:
        print("Hospitals error:", e)
        return jsonify([])

@app.route('/api/hospitals/<int:hid>', methods=['GET'])
def get_hospital(hid):
    h = Hospital.query.get_or_404(hid)
    return jsonify(hospital_dict(h))

@app.route('/api/hospitals/<int:hid>', methods=['PUT'])
def update_hospital(hid):
    uid = session.get('user_id')
    if not uid: return jsonify({'error': 'Not logged in'}), 401
    h = Hospital.query.filter_by(id=hid, user_id=uid).first()
    if not h: return jsonify({'error': 'Not authorized'}), 403
    d = request.json or {}
    for field in ['name','address','city','state','pincode','phone','h_type','beds','established','description']:
        if field in d: setattr(h, field, d[field])
    if 'services' in d:
        s = d['services']
        if isinstance(s, str): s = [x.strip() for x in s.split(',') if x.strip()]
        h.services = json.dumps(s)
    if 'doctors' in d:
        h.doctors_json = json.dumps(d['doctors'])
    db.session.commit()
    return jsonify(hospital_dict(h))

# ─────────────── APPOINTMENTS ───────────────

@app.route('/api/appointments', methods=['POST'])
def book_appointment():
    uid = session.get('user_id')
    if not uid: return jsonify({'error': 'Not logged in'}), 401
    d = request.json or {}
    required = ['hospital_id','doctor_name','slot_date','slot_time']
    missing = [f for f in required if not str(d.get(f,'')).strip()]
    if missing: return jsonify({'error': f'Missing: {", ".join(missing)}'}), 400
    a = Appointment(
        user_id=uid, hospital_id=int(d['hospital_id']),
        doctor_name=d['doctor_name'], doctor_spec=d.get('doctor_spec',''),
        slot_date=d['slot_date'], slot_time=d['slot_time'],
        fee=int(d.get('fee',0)), notes=d.get('notes',''),
        reminder_at=d.get('reminder_at','')
    )
    db.session.add(a)
    db.session.commit()
    return jsonify({'success': True, 'appointment': appt_dict(a)})

@app.route('/api/appointments/user', methods=['GET'])
def user_appointments():
    uid = session.get('user_id')
    if not uid: return jsonify({'error': 'Not logged in'}), 401
    appts = Appointment.query.filter_by(user_id=uid).order_by(Appointment.created_at.desc()).all()
    return jsonify([appt_dict(a) for a in appts])

@app.route('/api/appointments/hospital', methods=['GET'])
def hospital_appointments():
    uid = session.get('user_id')
    if not uid: return jsonify({'error': 'Not logged in'}), 401
    h = Hospital.query.filter_by(user_id=uid).first()
    if not h: return jsonify({'error': 'Hospital not found'}), 404
    appts = Appointment.query.filter_by(hospital_id=h.id).order_by(Appointment.created_at.desc()).all()
    return jsonify([appt_dict(a) for a in appts])

@app.route('/api/appointments/<int:aid>/status', methods=['PUT'])
def update_appt_status(aid):
    uid = session.get('user_id')
    if not uid: return jsonify({'error': 'Not logged in'}), 401
    a = Appointment.query.get_or_404(aid)
    d = request.json or {}
    a.status = d.get('status', a.status)
    db.session.commit()
    return jsonify({'success': True, 'appointment': appt_dict(a)})

# ─────────────── REMINDERS ───────────────

@app.route('/api/reminders', methods=['GET'])
def get_reminders():
    uid = session.get('user_id')
    if not uid: return jsonify({'error': 'Not logged in'}), 401
    rems = Reminder.query.filter_by(user_id=uid, active=True).all()
    return jsonify([{'id':r.id,'name':r.name,'time_str':r.time_str,'frequency':r.frequency,'active':r.active} for r in rems])

@app.route('/api/reminders', methods=['POST'])
def add_reminder():
    uid = session.get('user_id')
    if not uid: return jsonify({'error': 'Not logged in'}), 401
    d = request.json or {}
    if not d.get('name') or not d.get('time_str'):
        return jsonify({'error': 'Name and time required'}), 400
    r = Reminder(user_id=uid, name=d['name'], time_str=d['time_str'], frequency=d.get('frequency','Daily'))
    db.session.add(r)
    db.session.commit()
    return jsonify({'success': True, 'reminder': {'id':r.id,'name':r.name,'time_str':r.time_str,'frequency':r.frequency}})

@app.route('/api/reminders/<int:rid>', methods=['DELETE'])
def delete_reminder(rid):
    uid = session.get('user_id')
    if not uid: return jsonify({'error': 'Not logged in'}), 401
    r = Reminder.query.filter_by(id=rid, user_id=uid).first()
    if r:
        r.active = False
        db.session.commit()
    return jsonify({'success': True})

# ─────────────── AI ───────────────

@app.route('/api/ai/chat', methods=['POST'])
def ai_chat():
    try:
        data = request.json or {}
        messages = data.get("messages", [])
        uid = session.get("user_id")
        user = User.query.get(uid) if uid else None
        user_age = user.age if user else 25

        user_msg = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                user_msg = m.get("content", "")
                break
        msg = user_msg.lower()

        hospitals = Hospital.query.all()
        doctor_context_lines = []
        for h in hospitals:
            doctors = json.loads(h.doctors_json or "[]")
            for d in doctors:
                spec = d.get('specialty') or d.get('specialization','')
                doctor_context_lines.append(f"Dr. {d['name']} ({spec}) at {h.name}, {h.city}")
        doctor_context = "\n".join(doctor_context_lines)

        if "cancel" in msg:
            appt = Appointment.query.filter_by(user_id=uid).order_by(Appointment.id.desc()).first()
            if appt and appt.status not in ['cancelled','rejected']:
                appt.status = "cancelled"
                db.session.commit()
                return jsonify({"reply": f"Your appointment with {appt.doctor_name} on {appt.slot_date} at {appt.slot_time} has been cancelled."})
            return jsonify({"reply": "No active appointment found to cancel."})

        is_booking = any(x in msg for x in ["book","appointment","doctor","consult","hospital"])
        if is_booking:
            for h in hospitals:
                doctors = json.loads(h.doctors_json or "[]")
                for d in doctors:
                    if d["name"].lower() in msg:
                        appt = Appointment(
                            user_id=uid, hospital_id=h.id,
                            doctor_name=d["name"],
                            doctor_spec=d.get('specialty') or d.get('specialization',''),
                            slot_date=date.today().isoformat(),
                            slot_time="10:00 AM", status="pending"
                        )
                        db.session.add(appt)
                        db.session.commit()
                        return jsonify({"reply": f"✅ Appointment requested!\n\n👨‍⚕️ Doctor: {d['name']}\n🏥 Hospital: {h.name}\n📅 Date: {date.today().isoformat()}\n⏰ Time: 10:00 AM\n\nStatus: Pending hospital confirmation."})
            return jsonify({"reply": f"I can help you book an appointment. Here are our available doctors:\n\n{doctor_context}\n\nPlease mention the doctor's name or go to the Hospitals section to book directly."})

        def simple_fallback(msg):
            if any(x in msg for x in ["stress","anxious","anxiety"]): return "Try deep breathing: inhale 4s, hold 4s, exhale 6s. A 10-minute walk also helps greatly. If persistent, consider speaking with a professional."
            if "back pain" in msg: return "Rest, apply warm compress, avoid heavy lifting. Gentle stretching helps. See a doctor if pain lasts more than 3 days."
            if any(x in msg for x in ["fever","temperature"]): return "Rest and stay hydrated. Monitor temperature. If above 103°F / 39.4°C or lasting more than 3 days, please see a doctor."
            if any(x in msg for x in ["sugar","diabetes"]): return "Avoid refined sugars and white rice. Eat fiber-rich foods — vegetables, whole grains, legumes. Stay hydrated and walk 30 mins daily."
            if any(x in msg for x in ["chest","heart"]): return "⚠️ Chest pain can be serious. If severe or accompanied by shortness of breath, please go to the emergency room immediately or call emergency services."
            if any(x in msg for x in ["headache","head"]): return "Rest in a quiet, dark room. Stay hydrated. A cold or warm compress on your forehead may help. If severe or sudden, see a doctor."
            if any(x in msg for x in ["cold","cough","flu"]): return "Rest, drink warm fluids, honey-ginger tea helps. Steam inhalation for congestion. See a doctor if symptoms worsen after 5 days."
            return f"I'm here to help with your health questions. Based on your age ({user_age}), I can give personalized advice. Please describe your symptoms in detail."

        try:
            if client:
                system_prompt = f"""You are MedCare AI, a helpful health assistant app.
User age: {user_age} years old.
Available doctors in our network:
{doctor_context}

Rules:
- Give helpful, clear health advice appropriate for age {user_age}
- For serious symptoms (chest pain, difficulty breathing), always recommend emergency care
- For booking appointments, refer to the doctors listed above
- Keep responses concise and friendly
- Use emojis sparingly for readability
- Never diagnose — suggest and recommend professional consultation"""
                response = client.chat.completions.create(
                    model="llama-3.1-70b-versatile",
                    messages=[{"role":"system","content":system_prompt}] + messages,
                    temperature=0.6,
                    max_tokens=500
                )
                reply = response.choices[0].message.content
            else:
                reply = simple_fallback(msg)
        except Exception as e:
            print("Groq error:", e)
            reply = simple_fallback(msg)

        return jsonify({"reply": reply})
    except Exception as e:
        print("AI crash:", e)
        return jsonify({"reply": "I'm having trouble right now. Please try again."}), 200

@app.route('/api/ai/calories', methods=['POST'])
def ai_calories():
    try:
        uid = session.get('user_id')
        if not uid: return jsonify({'error': 'Not logged in'}), 401
        d = request.json or {}
        description = d.get('description', '')
        if not description: return jsonify({'error': 'No description'}), 400
        if client is None:
            return jsonify({'total_calories':400,'total_protein':15,'total_carbs':50,'total_fat':12,'items':[{'name':description,'calories':400}]})
        response = client.chat.completions.create(
            model="llama-3.1-70b-versatile",
            messages=[
                {"role":"system","content":"You are a nutrition expert. Return ONLY valid JSON. No markdown, no backticks, no explanation. Format: {\"total_calories\":number,\"total_protein\":number,\"total_carbs\":number,\"total_fat\":number,\"items\":[{\"name\":string,\"calories\":number}]}"},
                {"role":"user","content":f"Estimate nutrition for: {description}"}
            ],
            max_tokens=400
        )
        text = response.choices[0].message.content.strip()
        text = text.replace('```json','').replace('```','').strip()
        data = json.loads(text)
        return jsonify(data)
    except Exception as e:
        print("Calories error:", e)
        return jsonify({'total_calories':350,'total_protein':12,'total_carbs':45,'total_fat':10,'items':[{'name':'Estimated meal','calories':350}]})

# ─────────────── SEED DATA ───────────────

@app.route('/api/init-data')
def init_data():
    if Hospital.query.first():
        return jsonify({"msg": "Already initialized"})

    seed = [
        {
            "name": "District Hospital Tumakuru",
            "address": "B.H. Road, Near Bus Stand",
            "city": "Tumkur",
            "phone": "0816-2271234",
            "h_type": "Government",
            "beds": 300,
            "established": 1960,
            "reg_number": "GOV-TK-001",
            "services": ["General Medicine","Pediatrics","Emergency","Surgery","Maternity","Orthopedics"],
            "doctors": [
                {"name": "Dr. Ravi Kumar","specialty": "General Medicine","experience": 15,"fee": 200,"slots": ["09:00 AM","11:00 AM","03:00 PM"]},
                {"name": "Dr. Anita S","specialty": "Pediatrics","experience": 10,"fee": 250,"slots": ["10:00 AM","02:00 PM"]}
            ]
        },
        {
            "name": "Siddaganga Hospital",
            "address": "Siddaganga Road",
            "city": "Tumkur",
            "phone": "0816-2277890",
            "h_type": "Trust",
            "beds": 200,
            "established": 1980,
            "reg_number": "TRS-TK-002",
            "services": ["Cardiology","Neurology","Orthopedics","ICU"],
            "doctors": [
                {"name": "Dr. Mohan R","specialty": "Cardiology","experience": 20,"fee": 500,"slots": ["09:30 AM","01:00 PM","04:00 PM"]},
                {"name": "Dr. Priya K","specialty": "Neurology","experience": 12,"fee": 450,"slots": ["10:30 AM","03:30 PM"]}
            ]
        },
        {
            "name": "Shridevi Hospital",
            "address": "Shridevi Nagar",
            "city": "Tumkur",
            "phone": "0816-2265432",
            "h_type": "Private",
            "beds": 150,
            "established": 1995,
            "reg_number": "PVT-TK-003",
            "services": ["Orthopedics","ENT","Dermatology","Physiotherapy"],
            "doctors": [
                {"name": "Dr. Shankar B","specialty": "Orthopedics","experience": 18,"fee": 400,"slots": ["09:00 AM","12:00 PM","05:00 PM"]}
            ]
        },
        {
            "name": "Adarsha Nursing Home",
            "address": "Gandhi Nagar",
            "city": "Tumkur",
            "phone": "0816-2289876",
            "h_type": "Private",
            "beds": 50,
            "established": 2005,
            "reg_number": "PVT-TK-004",
            "services": ["Dermatology","Gynecology","General Medicine"],
            "doctors": [
                {"name": "Dr. Suresh M","specialty": "Dermatology","experience": 12,"fee": 350,"slots": ["10:00 AM","02:00 PM","06:00 PM"]}
            ]
        },
        {
            "name": "Vinayaka Hospital",
            "address": "Vinayaka Circle",
            "city": "Tumkur",
            "phone": "0816-2254321",
            "h_type": "Private",
            "beds": 80,
            "established": 2010,
            "reg_number": "PVT-TK-005",
            "services": ["General Medicine","Cardiology","Diabetes Care"],
            "doctors": [
                {"name": "Dr. Arjun V","specialty": "General Physician","experience": 10,"fee": 300,"slots": ["09:00 AM","11:30 AM","03:00 PM"]}
            ]
        }
    ]

    admin = User(
        name="Admin User",
        email="admin@medcare.com",
        password=generate_password_hash("admin123"),
        role="user",
        age=30,
        gender="Male"
    )
    db.session.add(admin)
    db.session.flush()

    for s in seed:
        u = User(
            name=s['name'],
            email=s['name'].lower().replace(' ','_') + "@medcare.com",
            password=generate_password_hash("hospital123"),
            role="hospital"
        )
        db.session.add(u)
        db.session.flush()
        h = Hospital(
            user_id=u.id, name=s['name'], address=s['address'],
            city=s['city'], state='Karnataka', phone=s['phone'],
            h_type=s['h_type'], beds=s['beds'], established=s['established'],
            reg_number=s['reg_number'],
            services=json.dumps(s['services']),
            doctors_json=json.dumps(s['doctors'])
        )
        db.session.add(h)

    db.session.commit()
    return jsonify({"msg": "Seeded successfully!"})