from flask import Flask, jsonify, request, session, render_template, send_from_directory
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date
from dotenv import load_dotenv
from groq import Groq
import os, json, secrets

load_dotenv()
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///medcare.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = False
CORS(app, supports_credentials=True, origins=["http://localhost:5001", "http://127.0.0.1:5001"])
db = SQLAlchemy(app)

client = None
if GROQ_API_KEY:
    client = Groq(api_key=GROQ_API_KEY)
    print("✅ GROQ READY")
else:
    print("⚠️  GROQ KEY MISSING — fallback mode")


# ─────────────── MODELS ───────────────

class User(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(120), nullable=False)
    username   = db.Column(db.String(80), unique=True, nullable=False)   # replaces email for login
    password   = db.Column(db.String(256), nullable=False)
    role       = db.Column(db.String(20), default='user')
    age        = db.Column(db.Integer, default=25)
    gender     = db.Column(db.String(20))
    phone      = db.Column(db.String(20))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

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
    id          = db.Column(db.Integer, primary_key=True)
    user_id     = db.Column(db.Integer)
    hospital_id = db.Column(db.Integer)
    doctor_name = db.Column(db.String(120))
    doctor_spec = db.Column(db.String(100))
    slot_date   = db.Column(db.String(30))
    slot_time   = db.Column(db.String(20))
    fee         = db.Column(db.Integer, default=0)
    notes       = db.Column(db.Text, default='')
    status      = db.Column(db.String(20), default='pending')
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)

class Reminder(db.Model):
    id        = db.Column(db.Integer, primary_key=True)
    user_id   = db.Column(db.Integer)
    name      = db.Column(db.String(200))
    time_str  = db.Column(db.String(10))
    frequency = db.Column(db.String(30), default='Daily')
    active    = db.Column(db.Boolean, default=True)

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
    return {'id': u.id, 'name': u.name, 'username': u.username,
            'role': u.role, 'age': u.age, 'gender': u.gender, 'phone': u.phone}

def hospital_dict(h):
    doctors = json.loads(h.doctors_json or '[]')
    for d in doctors:
        if 'specialization' in d and 'specialty' not in d:
            d['specialty'] = d['specialization']
        if 'specialty' in d and 'specialization' not in d:
            d['specialization'] = d['specialty']
    return {
        'id': h.id, 'name': h.name, 'reg_number': h.reg_number,
        'address': h.address, 'city': h.city, 'state': h.state, 'pincode': h.pincode,
        'phone': h.phone, 'h_type': h.h_type, 'beds': h.beds,
        'established': h.established, 'description': h.description,
        'services': json.loads(h.services or '[]'),
        'doctors': doctors
    }

def appt_dict(a):
    hosp = Hospital.query.get(a.hospital_id)
    user = User.query.get(a.user_id)
    return {
        'id': a.id, 'user_id': a.user_id, 'hospital_id': a.hospital_id,
        'user_name': user.name if user else 'Unknown',
        'hospital_name': hosp.name if hosp else 'Unknown',
        'doctor_name': a.doctor_name, 'doctor_spec': a.doctor_spec,
        'slot_date': a.slot_date, 'slot_time': a.slot_time,
        'fee': a.fee, 'notes': a.notes,
        'status': a.status, 'created_at': str(a.created_at)
    }


# ─────────────── AUTH ───────────────

@app.route('/api/signup/user', methods=['POST'])
def signup_user():
    d = request.json or {}
    name     = (d.get('name') or '').strip()
    username = (d.get('username') or '').strip().lower()
    password = d.get('password') or ''
    age      = d.get('age')
    gender   = (d.get('gender') or '').strip()
    phone    = (d.get('phone') or '').strip()

    if not name or not username or not password or not age or not gender:
        return jsonify({'error': 'Please fill all required fields'}), 400
    if len(password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters'}), 400
    if User.query.filter_by(username=username).first():
        return jsonify({'error': f'Username "{username}" is already taken'}), 409

    u = User(name=name, username=username,
             password=generate_password_hash(password),
             role='user', age=int(age), gender=gender, phone=phone)
    db.session.add(u)
    db.session.commit()
    session['user_id'] = u.id
    session['role']    = 'user'
    return jsonify({'success': True, 'user': user_dict(u)})


@app.route('/api/signup/hospital', methods=['POST'])
def signup_hospital():
    d = request.json or {}
    name     = (d.get('name') or '').strip()
    username = (d.get('username') or '').strip().lower()
    password = d.get('password') or ''
    address  = (d.get('address') or '').strip()
    phone    = (d.get('phone') or '').strip()
    reg      = (d.get('reg_number') or '').strip()

    if not name or not username or not password or not address or not phone:
        return jsonify({'error': 'Please fill all required fields'}), 400
    if len(password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters'}), 400
    if User.query.filter_by(username=username).first():
        return jsonify({'error': f'Username "{username}" is already taken'}), 409

    u = User(name=name, username=username,
             password=generate_password_hash(password), role='hospital')
    db.session.add(u)
    db.session.flush()

    services = d.get('services', [])
    if isinstance(services, str):
        services = [s.strip() for s in services.split(',') if s.strip()]

    doctors = d.get('doctors', [])

    h = Hospital(
        user_id=u.id, name=name, reg_number=reg,
        address=address, city=d.get('city', 'Tumkur'),
        state=d.get('state', 'Karnataka'), pincode=d.get('pincode', ''),
        phone=phone, h_type=d.get('h_type', 'Private'),
        beds=int(d.get('beds') or 100),
        established=int(d.get('established') or datetime.now().year),
        description=d.get('description', ''),
        services=json.dumps(services),
        doctors_json=json.dumps(doctors)
    )
    db.session.add(h)
    db.session.commit()
    session['user_id']    = u.id
    session['role']       = 'hospital'
    session['hospital_id'] = h.id
    return jsonify({'success': True, 'user': user_dict(u), 'hospital': hospital_dict(h)})


@app.route('/api/login', methods=['POST'])
def login():
    d = request.json or {}
    username = (d.get('username') or '').strip().lower()
    password  = d.get('password') or ''

    if not username or not password:
        return jsonify({'error': 'Username and password are required'}), 400

    u = User.query.filter_by(username=username).first()
    if not u:
        return jsonify({'error': 'No account found with that username'}), 401
    if not check_password_hash(u.password, password):
        return jsonify({'error': 'Incorrect password'}), 401

    session.clear()
    session['user_id'] = u.id
    session['role']    = u.role

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
    if not uid:
        return jsonify({'error': 'Not logged in'}), 401
    u = User.query.get(uid)
    if not u:
        return jsonify({'error': 'User not found'}), 404
    result = {'user': user_dict(u)}
    if u.role == 'hospital':
        h = Hospital.query.filter_by(user_id=uid).first()
        if h:
            result['hospital'] = hospital_dict(h)
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
        'name': d.get('name', ''), 'cal': int(d.get('cal', 0)),
        'protein': float(d.get('protein', 0)), 'carbs': float(d.get('carbs', 0)),
        'fat': float(d.get('fat', 0)), 'id': int(datetime.utcnow().timestamp() * 1000)
    }
    food_list.append(item)
    log.food_log     = json.dumps(food_list)
    log.cal_consumed = (log.cal_consumed or 0) + item['cal']
    log.protein      = (log.protein or 0) + item['protein']
    log.carbs        = (log.carbs or 0) + item['carbs']
    log.fat          = (log.fat or 0) + item['fat']
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/health/food/<int:item_id>', methods=['DELETE'])
def delete_food(item_id):
    uid = session.get('user_id')
    if not uid: return jsonify({'error': 'Not logged in'}), 401
    log = get_or_create_log(uid)
    food_list = json.loads(log.food_log or '[]')
    item = next((f for f in food_list if f.get('id') == item_id), None)
    if item:
        food_list        = [f for f in food_list if f.get('id') != item_id]
        log.food_log     = json.dumps(food_list)
        log.cal_consumed = max(0, (log.cal_consumed or 0) - item['cal'])
        log.protein      = max(0, (log.protein or 0) - item['protein'])
        log.carbs        = max(0, (log.carbs or 0) - item['carbs'])
        log.fat          = max(0, (log.fat or 0) - item['fat'])
        db.session.commit()
    return jsonify({'success': True})


@app.route('/api/health/exercise', methods=['POST'])
def toggle_exercise():
    uid = session.get('user_id')
    if not uid: return jsonify({'error': 'Not logged in'}), 401
    d   = request.json or {}
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
    if 'cal_goal' in d:
        log.cal_goal = int(d['cal_goal'])
    db.session.commit()
    return jsonify({'success': True})


# ─────────────── HOSPITALS ───────────────

@app.route('/api/hospitals', methods=['GET'])
def get_hospitals():
    hospitals = Hospital.query.all()
    return jsonify([hospital_dict(h) for h in hospitals])


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
    for field in ['name', 'address', 'city', 'state', 'pincode', 'phone',
                  'h_type', 'beds', 'established', 'description']:
        if field in d:
            setattr(h, field, d[field])
    if 'services' in d:
        s = d['services']
        if isinstance(s, str):
            s = [x.strip() for x in s.split(',') if x.strip()]
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
    if not d.get('hospital_id') or not d.get('doctor_name') or not d.get('slot_date') or not d.get('slot_time'):
        return jsonify({'error': 'Missing required fields'}), 400
    a = Appointment(
        user_id=uid, hospital_id=int(d['hospital_id']),
        doctor_name=d['doctor_name'], doctor_spec=d.get('doctor_spec', ''),
        slot_date=d['slot_date'], slot_time=d['slot_time'],
        fee=int(d.get('fee', 0)), notes=d.get('notes', '')
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
    return jsonify([{'id': r.id, 'name': r.name, 'time_str': r.time_str, 'frequency': r.frequency} for r in rems])


@app.route('/api/reminders', methods=['POST'])
def add_reminder():
    uid = session.get('user_id')
    if not uid: return jsonify({'error': 'Not logged in'}), 401
    d = request.json or {}
    if not d.get('name') or not d.get('time_str'):
        return jsonify({'error': 'Name and time required'}), 400
    r = Reminder(user_id=uid, name=d['name'], time_str=d['time_str'], frequency=d.get('frequency', 'Daily'))
    db.session.add(r)
    db.session.commit()
    return jsonify({'success': True, 'reminder': {'id': r.id, 'name': r.name, 'time_str': r.time_str, 'frequency': r.frequency}})


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
        d        = request.json or {}
        messages = d.get('messages', [])
        uid      = session.get('user_id')
        user     = User.query.get(uid) if uid else None
        age      = user.age if user else 25

        user_msg = next((m['content'] for m in reversed(messages) if m.get('role') == 'user'), '')
        msg      = user_msg.lower()

        hospitals = Hospital.query.all()
        doc_lines = []
        for h in hospitals:
            for d2 in json.loads(h.doctors_json or '[]'):
                spec = d2.get('specialty') or d2.get('specialization', '')
                doc_lines.append(f"Dr. {d2['name']} ({spec}) at {h.name} — ₹{d2.get('fee',0)} — Slots: {', '.join(d2.get('slots',[]))}")
        doctor_context = '\n'.join(doc_lines)

        # Cancel appointment
        if 'cancel' in msg:
            appt = Appointment.query.filter_by(user_id=uid).order_by(Appointment.id.desc()).first()
            if appt and appt.status not in ['cancelled', 'rejected']:
                appt.status = 'cancelled'
                db.session.commit()
                return jsonify({'reply': f'Your appointment with {appt.doctor_name} on {appt.slot_date} at {appt.slot_time} has been cancelled.'})
            return jsonify({'reply': 'No active appointment found to cancel.'})

        # Book via AI
        if any(x in msg for x in ['book', 'appointment', 'consult', 'see a doctor', 'visit doctor']):
            for h in hospitals:
                for d2 in json.loads(h.doctors_json or '[]'):
                    if d2['name'].lower() in msg:
                        slots = d2.get('slots', ['10:00 AM'])
                        a = Appointment(
                            user_id=uid, hospital_id=h.id,
                            doctor_name=d2['name'],
                            doctor_spec=d2.get('specialty') or d2.get('specialization', ''),
                            slot_date=date.today().isoformat(),
                            slot_time=slots[0], status='pending', fee=int(d2.get('fee', 0))
                        )
                        db.session.add(a)
                        db.session.commit()
                        return jsonify({'reply': f'✅ Appointment requested!\n\n👨‍⚕️ Doctor: {d2["name"]}\n🏥 Hospital: {h.name}\n📅 Date: {date.today().isoformat()}\n⏰ Time: {slots[0]}\n💰 Fee: ₹{d2.get("fee",0)}\n\nStatus: Pending hospital confirmation. Check the Appointments tab!'})
            return jsonify({'reply': f'I can help you book! Here are our available doctors:\n\n{doctor_context}\n\nSay "Book Dr. [name]" to book instantly, or visit the Hospitals section.'})

        def fallback(msg, age):
            if any(x in msg for x in ['stress', 'anxious', 'anxiety', 'tension']):
                return f'For stress at age {age}: try deep breathing (4-4-6 count), 20-min walks, and reduce screen time before bed. If persistent, see a psychiatrist. Our network has options — just ask!'
            if 'back pain' in msg:
                return 'For back pain: rest for 1–2 days, apply warm compress, avoid heavy lifting. Cat-cow stretches help. If pain lasts >1 week or shoots to the leg, see an orthopedist.'
            if any(x in msg for x in ['fever', 'temperature', 'chills']):
                return 'For fever: rest, hydrate well (ORS/coconut water), paracetamol for relief. See a doctor if >103°F / 39.4°C, lasts >3 days, or comes with rash/breathing difficulty.'
            if any(x in msg for x in ['sugar', 'diabetes', 'blood glucose']):
                return 'For high blood sugar: avoid white rice, sugar, maida. Eat more vegetables, dal, ragi, and whole grains. Walk 30 min daily. Monitor levels regularly.'
            if any(x in msg for x in ['chest pain', 'chest', 'heart']):
                return '⚠️ IMPORTANT: Chest pain can be serious. If the pain is severe, crushing, spreads to your arm/jaw, or comes with sweating — go to Emergency IMMEDIATELY. Do not wait.'
            if any(x in msg for x in ['headache', 'migraine', 'head pain']):
                return 'For headache: rest in a dark quiet room, stay hydrated, cold compress on forehead. If sudden & severe (thunderclap), with vision changes, or after head injury — see a doctor urgently.'
            if any(x in msg for x in ['cold', 'cough', 'runny nose', 'flu', 'sore throat']):
                return 'For cold/cough: steam inhalation, warm turmeric milk, honey-ginger tea, rest. Stay away from cold drinks. See a doctor if fever persists >3 days or breathlessness occurs.'
            if any(x in msg for x in ['skin', 'rash', 'itch', 'acne']):
                return 'For skin issues: keep the area clean, avoid scratching, use mild soap. A dermatologist can diagnose properly. We have skin doctors available — want to book?'
            return f'Hello! I\'m MedCare AI, here for your health questions. You\'re {age} years old, so I give age-appropriate advice.\n\nOur doctors:\n{doctor_context[:400]}...\n\nDescribe your symptoms and I\'ll help!'

        try:
            if client:
                system = f"""You are MedCare AI, a helpful health assistant for Tumkur, Karnataka.
User age: {age} years. Give age-appropriate advice.

Available doctors:
{doctor_context}

Rules:
- Give practical home remedies first, then recommend a doctor if needed
- For emergencies (chest pain, breathing issues, stroke signs) → direct to ER immediately
- Be warm, conversational, and concise
- For bookings, reference the doctor list above
- Never diagnose — advise and recommend professional consultation
- Use emojis occasionally for warmth"""
                response = client.chat.completions.create(
                    model='llama-3.1-70b-versatile',
                    messages=[{'role': 'system', 'content': system}] + messages,
                    temperature=0.6, max_tokens=500
                )
                return jsonify({'reply': response.choices[0].message.content})
        except Exception as e:
            print('Groq error:', e)

        return jsonify({'reply': fallback(msg, age)})
    except Exception as e:
        print('AI crash:', e)
        return jsonify({'reply': 'I\'m having trouble right now. Please try again in a moment.'})


@app.route('/api/ai/calories', methods=['POST'])
def ai_calories():
    uid = session.get('user_id')
    if not uid: return jsonify({'error': 'Not logged in'}), 401
    d = request.json or {}
    desc = d.get('description', '')
    if not desc: return jsonify({'error': 'No description'}), 400

    if not client:
        return jsonify({'total_calories': 400, 'total_protein': 15, 'total_carbs': 50, 'total_fat': 12,
                        'items': [{'name': desc, 'calories': 400}]})
    try:
        response = client.chat.completions.create(
            model='llama-3.1-70b-versatile',
            messages=[
                {'role': 'system', 'content': 'You are a nutrition expert. Return ONLY valid JSON with no markdown or backticks. Format: {"total_calories":number,"total_protein":number,"total_carbs":number,"total_fat":number,"items":[{"name":string,"calories":number}]}'},
                {'role': 'user', 'content': f'Estimate nutrition for: {desc}'}
            ], max_tokens=400
        )
        text = response.choices[0].message.content.strip().replace('```json', '').replace('```', '').strip()
        return jsonify(json.loads(text))
    except Exception as e:
        print('Calories error:', e)
        return jsonify({'total_calories': 350, 'total_protein': 12, 'total_carbs': 45, 'total_fat': 10,
                        'items': [{'name': 'Estimated meal', 'calories': 350}]})


# ─────────────── SEED DATA (5 real Tumkur hospitals) ───────────────

@app.route('/api/init-data')
def init_data():
    if Hospital.query.first():
        return jsonify({'msg': 'Already initialized — data exists'})

    SEED_HOSPITALS = [
        {
            'name': 'Kasturba Hospital Tumkur',
            'address': 'No. 1/1, B.H. Road',
            'city': 'Tumkur', 'pincode': '572101', 'phone': '0816-2254555',
            'h_type': 'Private', 'beds': 150, 'established': 1993,
            'reg_number': 'PVT-TK-KH-001',
            'description': 'A leading multi-specialty hospital in Tumkur established in 1993. Known for OBG, internal medicine, ICU, and emergency care. Run by Dr Durgadas Asranna.',
            'services': ['Obstetrics & Gynecology', 'Internal Medicine', 'Emergency', 'ICU', 'Pediatrics', 'Surgery', 'ENT', 'Orthopedics'],
            'doctors': [
                {'name': 'Dr. Durgadas Asranna', 'specialty': 'Obstetrics & Gynecology', 'experience': 30, 'fee': 400, 'slots': ['09:00 AM', '11:00 AM', '04:00 PM']},
                {'name': 'Dr. Kavitha Murthy', 'specialty': 'Internal Medicine', 'experience': 18, 'fee': 300, 'slots': ['10:00 AM', '02:00 PM', '05:00 PM']},
                {'name': 'Dr. Suresh Reddy', 'specialty': 'Pediatrics', 'experience': 14, 'fee': 250, 'slots': ['09:30 AM', '01:00 PM']},
                {'name': 'Dr. Jyothi Swarup', 'specialty': 'ENT', 'experience': 22, 'fee': 350, 'slots': ['11:00 AM', '03:30 PM']},
            ]
        },
        {
            'name': 'Sree Siddhartha Medical College & Hospital',
            'address': 'Agalakote, B.H. Road',
            'city': 'Tumkur', 'pincode': '572107', 'phone': '0816-2274000',
            'h_type': 'Teaching', 'beds': 750, 'established': 2003,
            'reg_number': 'TEACH-TK-SSM-002',
            'description': 'Premier teaching hospital affiliated to Sri Siddhartha University. Offers comprehensive super-specialty and multi-specialty care with state-of-the-art equipment.',
            'services': ['Cardiology', 'Neurology', 'Orthopedics', 'Surgery', 'Dermatology', 'Psychiatry', 'Oncology', 'Urology', 'Nephrology'],
            'doctors': [
                {'name': 'Dr. G.N. Prabhakara', 'specialty': 'Surgery', 'experience': 28, 'fee': 500, 'slots': ['09:00 AM', '12:00 PM', '04:00 PM']},
                {'name': 'Dr. Shivanand D.R.', 'specialty': 'Dermatology', 'experience': 20, 'fee': 400, 'slots': ['10:00 AM', '02:30 PM']},
                {'name': 'Dr. Kumar G.V.', 'specialty': 'Pediatrics', 'experience': 25, 'fee': 300, 'slots': ['09:00 AM', '01:00 PM', '05:00 PM']},
                {'name': 'Dr. Natraj G.', 'specialty': 'General Medicine', 'experience': 22, 'fee': 350, 'slots': ['10:30 AM', '03:00 PM']},
                {'name': 'Dr. Deepali A.', 'specialty': 'Physiology / Wellness', 'experience': 15, 'fee': 300, 'slots': ['11:00 AM', '04:00 PM']},
            ]
        },
        {
            'name': 'District Hospital Tumakuru (Government)',
            'address': 'B.H. Road, Near Bus Stand',
            'city': 'Tumkur', 'pincode': '572101', 'phone': '0816-2271234',
            'h_type': 'Government', 'beds': 400, 'established': 1958,
            'reg_number': 'GOV-TK-DH-003',
            'description': 'The primary government hospital for Tumkur district providing free and subsidised healthcare to all. 24x7 emergency, OPD, maternity, and surgical services.',
            'services': ['General Medicine', 'Emergency 24x7', 'Maternity', 'Pediatrics', 'Surgery', 'Ophthalmology', 'Dental', 'Blood Bank'],
            'doctors': [
                {'name': 'Dr. Ravi Kumar', 'specialty': 'General Medicine', 'experience': 15, 'fee': 0, 'slots': ['09:00 AM', '11:00 AM', '02:00 PM']},
                {'name': 'Dr. Anitha S.', 'specialty': 'Obstetrics & Gynecology', 'experience': 12, 'fee': 0, 'slots': ['10:00 AM', '12:00 PM']},
                {'name': 'Dr. Basavaraju M.', 'specialty': 'Surgery', 'experience': 18, 'fee': 0, 'slots': ['09:00 AM', '01:00 PM']},
                {'name': 'Dr. Usha Rani', 'specialty': 'Pediatrics', 'experience': 10, 'fee': 0, 'slots': ['10:00 AM', '03:00 PM']},
            ]
        },
        {
            'name': 'Vinayaka Hospital Tumkur',
            'address': 'Vinayaka Circle, S.S. Puram',
            'city': 'Tumkur', 'pincode': '572102', 'phone': '0816-2254321',
            'h_type': 'Private', 'beds': 80, 'established': 2008,
            'reg_number': 'PVT-TK-VH-004',
            'description': 'Trusted multi-specialty hospital in S.S. Puram area, known for cardiology, diabetes care, and general medicine. Modern diagnostics with experienced doctors.',
            'services': ['Cardiology', 'Diabetes Care', 'General Medicine', 'Orthopedics', 'Neurology', 'Diagnostics', 'Pharmacy'],
            'doctors': [
                {'name': 'Dr. Mohan R.', 'specialty': 'Cardiology', 'experience': 20, 'fee': 500, 'slots': ['09:30 AM', '01:00 PM', '05:00 PM']},
                {'name': 'Dr. Arjun V.', 'specialty': 'General Physician', 'experience': 12, 'fee': 300, 'slots': ['09:00 AM', '11:30 AM', '04:30 PM']},
                {'name': 'Dr. Priya K.', 'specialty': 'Diabetology', 'experience': 10, 'fee': 350, 'slots': ['10:00 AM', '03:00 PM']},
                {'name': 'Dr. Shankar Rao', 'specialty': 'Neurology', 'experience': 16, 'fee': 450, 'slots': ['11:00 AM', '04:00 PM']},
            ]
        },
        {
            'name': 'Shridevi Institute of Medical Sciences',
            'address': 'Shetty Circle, Gubbi Road',
            'city': 'Tumkur', 'pincode': '572106', 'phone': '0816-2274555',
            'h_type': 'Teaching', 'beds': 500, 'established': 2000,
            'reg_number': 'TEACH-TK-SIMS-005',
            'description': 'A premier medical college and hospital offering affordable super-specialty care. Specialises in orthopedics, urology, and advanced surgical procedures.',
            'services': ['Orthopedics', 'Urology', 'General Surgery', 'Radiology', 'Physiotherapy', 'Oncology', 'Psychiatry', 'Nephrology'],
            'doctors': [
                {'name': 'Dr. Shankar B.', 'specialty': 'Orthopedics', 'experience': 18, 'fee': 400, 'slots': ['09:00 AM', '12:00 PM', '04:30 PM']},
                {'name': 'Dr. Neha Patil', 'specialty': 'ENT', 'experience': 9, 'fee': 300, 'slots': ['10:30 AM', '03:00 PM']},
                {'name': 'Dr. Venkatesh P.', 'specialty': 'Community Medicine', 'experience': 14, 'fee': 200, 'slots': ['09:00 AM', '01:00 PM']},
                {'name': 'Dr. Srinath K.', 'specialty': 'General Surgery', 'experience': 16, 'fee': 350, 'slots': ['10:00 AM', '02:00 PM', '05:30 PM']},
            ]
        }
    ]

    # Create a default patient account
    admin = User(name='Test Patient', username='patient',
                 password=generate_password_hash('patient123'),
                 role='user', age=30, gender='Male', phone='9876543210')
    db.session.add(admin)
    db.session.flush()

    for s in SEED_HOSPITALS:
        uname = s['name'].lower().replace(' ', '_').replace('(', '').replace(')', '')[:30]
        u = User(name=s['name'], username=uname,
                 password=generate_password_hash('hospital123'), role='hospital')
        db.session.add(u)
        db.session.flush()
        h = Hospital(
            user_id=u.id, name=s['name'], address=s['address'],
            city=s['city'], state='Karnataka', pincode=s['pincode'],
            phone=s['phone'], h_type=s['h_type'], beds=s['beds'],
            established=s['established'], reg_number=s['reg_number'],
            description=s['description'],
            services=json.dumps(s['services']),
            doctors_json=json.dumps(s['doctors'])
        )
        db.session.add(h)

    db.session.commit()
    return jsonify({'msg': '✅ Seeded! Patient login → username: patient / password: patient123'})


# ─────────────── SERVE ───────────────

@app.route('/')
@app.route('/<path:path>')
def serve(path=''):
    return render_template('index.html')


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)