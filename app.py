from flask import Flask, render_template, request, jsonify, Response, stream_with_context
import os, json, re
from groq import Groq

app = Flask(__name__)
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "GROQ_API_KEY")
client = Groq(api_key=GROQ_API_KEY)
MODEL = "llama-3.3-70b-versatile"

# ── AGENT SYSTEM PROMPTS ──────────────────────────────────────────────────────

AGENT_ANALYST = """You are the Farm Analyst Agent in a 4-agent agentic AI pipeline.
ONLY job: Analyze farm inputs and output structured JSON. Be precise. No markdown, ONLY valid JSON.
{
  "crop_stage": "exact stage name",
  "stage_description": "2-sentence description of plant biology at this stage",
  "risk_level": "Safe | Caution | Alert",
  "risk_reason": "specific reason based on weather + stage",
  "top_priority": "single most critical task for farmer today",
  "water_stress": "Low | Medium | High",
  "nutrient_phase": "what nutrients crop needs most right now"
}"""

AGENT_PLANNER = """You are the Action Planner Agent in a 4-agent agentic AI pipeline.
ONLY job: Given farm analysis, plan today's exact actions. ONLY valid JSON, no markdown.
{
  "action_today": "exact task in 1 sentence",
  "product": "exact Indian fertilizer/pesticide/input name",
  "quantity_per_acre": "exact amount with Indian unit",
  "application_method": "exactly how to apply",
  "best_time": "exact time window e.g. 6-8 AM",
  "skip_if": "condition to skip",
  "cost_estimate": "₹ amount",
  "saving_vs_standard": "₹ saved vs wasteful practice"
}"""

AGENT_FORECASTER = """You are the Schedule Forecaster Agent in a 4-agent agentic AI pipeline.
ONLY job: Output next 3 upcoming farm actions as JSON array. ONLY valid JSON array, no markdown.
[
  {"day_offset": 5, "action": "exact task", "reason": "why this timing"},
  {"day_offset": 12, "action": "exact task", "reason": "why this timing"},
  {"day_offset": 20, "action": "exact task", "reason": "why this timing"}
]"""

AGENT_WRITER = """You are CropAutopilot Message Writer — final agent in the pipeline.
You receive data from 3 upstream agents. Write the farmer-facing daily message.
Rules: emoji-heavy, conversational, actionable, under 400 words, include Hindi term in brackets.

Use this EXACT format:

🌾 CropAutopilot — Day {day}
━━━━━━━━━━━━━━━━━━━━━━━━━━

📍 {region}  |  🌤️ {weather_short}
🚦 Status: {risk_level}  |  🌱 Stage: {stage}

━━━━━━━━━━━━━━━━━━━━━━━━━━
🧠 ANALYST REPORT
━━━━━━━━━━━━━━━━━━━━━━━━━━
{stage_description}
⚠️ Top Priority: {top_priority}
💧 Water Stress: {water_stress}  |  🧪 Nutrient Focus: {nutrient_phase}

━━━━━━━━━━━━━━━━━━━━━━━━━━
⚡ TODAY'S ACTION
━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ Task: {action_today}
🧪 Product: {product}
💧 Dose: {quantity_per_acre}
🔧 Method: {application_method}
⏰ Best Time: {best_time}
⛔ Skip If: {skip_if}

━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 COST INTELLIGENCE
━━━━━━━━━━━━━━━━━━━━━━━━━━
💵 Estimated Cost: {cost_estimate}
📉 You Save: {saving_vs_standard}

━━━━━━━━━━━━━━━━━━━━━━━━━━
📅 UPCOMING SCHEDULE
━━━━━━━━━━━━━━━━━━━━━━━━━━
📌 Day +{d1}: {a1} — {r1}
📌 Day +{d2}: {a2} — {r2}
📌 Day +{d3}: {a3} — {r3}

━━━━━━━━━━━━━━━━━━━━━━━━━━
🤖 AutoPilot: 4 Agents Complete ✅ | Next check in 24hrs"""

# ── CROP STAGES ──────────────────────────────────────────────────────────────
CROP_STAGES = {
    "wheat":     [(0,10,"Germination"),(11,25,"Tillering"),(26,50,"Jointing"),(51,75,"Heading"),(76,100,"Grain Fill"),(101,150,"Maturity")],
    "rice":      [(0,15,"Nursery"),(16,30,"Transplanting"),(31,60,"Vegetative"),(61,85,"Reproductive"),(86,110,"Ripening"),(111,140,"Harvest")],
    "cotton":    [(0,15,"Germination"),(16,40,"Seedling"),(41,80,"Vegetative"),(81,110,"Flowering"),(111,140,"Boll Development"),(141,180,"Maturity")],
    "maize":     [(0,10,"Germination"),(11,25,"Seedling"),(26,50,"Vegetative"),(51,75,"Tasseling"),(76,100,"Grain Fill"),(101,125,"Maturity")],
    "sugarcane": [(0,30,"Germination"),(31,90,"Tillering"),(91,180,"Grand Growth"),(181,300,"Maturity"),(301,365,"Ripening"),(366,400,"Harvest")],
    "soybean":   [(0,10,"Germination"),(11,30,"Vegetative"),(31,60,"Flowering"),(61,80,"Pod Set"),(81,100,"Seed Fill"),(101,130,"Maturity")],
    "tomato":    [(0,15,"Seedling"),(16,35,"Vegetative"),(36,55,"Flowering"),(56,80,"Fruit Set"),(81,110,"Fruit Development"),(111,140,"Harvest")],
    "onion":     [(0,20,"Seedling"),(21,45,"Vegetative"),(46,70,"Bulb Initiation"),(71,100,"Bulb Development"),(101,130,"Maturity"),(131,150,"Harvest")],
    "mustard":   [(0,10,"Germination"),(11,25,"Rosette"),(26,45,"Bolting"),(46,65,"Flowering"),(66,90,"Pod Fill"),(91,120,"Maturity")],
    "potato":    [(0,15,"Sprout"),(16,30,"Vegetative"),(31,55,"Tuber Initiation"),(56,80,"Tuber Bulking"),(81,100,"Maturation"),(101,120,"Harvest")],
}

def get_crop_stage(crop, day):
    stages = CROP_STAGES.get(crop.lower(), [(0,40,"Early"),(41,80,"Mid"),(81,130,"Late"),(131,180,"Maturity")])
    for s, e, name in stages:
        if s <= day <= e:
            return name, min(round((day / stages[-1][1]) * 100), 100)
    return "Mature / Harvest", 95

def llm(system, user, temperature=0.3, max_tokens=600):
    r = client.chat.completions.create(
        model=MODEL,
        messages=[{"role":"system","content":system},{"role":"user","content":user}],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return r.choices[0].message.content.strip()

def parse_json(text):
    text = re.sub(r'```json|```', '', text).strip()
    for pattern in [r'\[.*\]', r'\{.*\}']:
        m = re.search(pattern, text, re.DOTALL)
        if m:
            try: return json.loads(m.group())
            except: pass
    try: return json.loads(text)
    except: return {}

# ── ROUTES ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/autopilot/stream", methods=["POST"])
def autopilot_stream():
    data       = request.json
    crop       = data.get("crop", "Wheat")
    day        = int(data.get("day", 1))
    region     = data.get("region", "Punjab")
    soil       = data.get("soil", "Loamy")
    weather    = data.get("weather", "Partly cloudy, no rain")
    area       = data.get("area", "1 acre")
    irrigation = data.get("irrigation", "Canal")
    stage, progress = get_crop_stage(crop, day)

    farm_ctx = (f"Crop: {crop} | Day {day} since sowing | Region: {region}, India | "
                f"Soil: {soil} | Weather next 3 days: {weather} | Farm size: {area} | "
                f"Irrigation: {irrigation} | Auto-detected stage: {stage} ({progress}% complete)")

    def generate():
        # AGENT 1
        yield f"data: {json.dumps({'type':'agent_start','agent':'Analyst','step':1,'msg':'🧠 Agent 1/4 — Farm Analyst is scanning conditions...'})}\n\n"
        try:
            raw = llm(AGENT_ANALYST, farm_ctx, 0.2, 500)
            analysis = parse_json(raw)
            if not isinstance(analysis, dict) or "crop_stage" not in analysis:
                analysis = {"crop_stage":stage,"stage_description":f"Crop is in {stage} phase. Monitor actively.","risk_level":"Safe","risk_reason":"Standard conditions","top_priority":"Follow regular schedule","water_stress":"Medium","nutrient_phase":"Balanced NPK"}
        except Exception as e:
            analysis = {"crop_stage":stage,"stage_description":f"{stage} phase — standard monitoring.","risk_level":"Safe","risk_reason":str(e)[:60],"top_priority":"Check field conditions","water_stress":"Medium","nutrient_phase":"NPK balance"}
        yield f"data: {json.dumps({'type':'agent_done','agent':'Analyst','step':1,'data':analysis})}\n\n"

        # AGENT 2
        yield f"data: {json.dumps({'type':'agent_start','agent':'Planner','step':2,'msg':'⚡ Agent 2/4 — Action Planner calculating exact inputs...'})}\n\n"
        try:
            raw = llm(AGENT_PLANNER, f"Farm: {farm_ctx}\nAnalysis: {json.dumps(analysis)}", 0.3, 500)
            plan = parse_json(raw)
            if not isinstance(plan, dict) or "action_today" not in plan:
                plan = {"action_today":"Irrigation and field inspection","product":"Water / Urea (as needed)","quantity_per_acre":"Check soil at 6-inch depth","application_method":"Flood or drip irrigation","best_time":"6–8 AM","skip_if":"Rainfall > 15mm","cost_estimate":"₹200–400","saving_vs_standard":"₹400 by avoiding overuse"}
        except:
            plan = {"action_today":"Routine field inspection","product":"N/A","quantity_per_acre":"N/A","application_method":"Manual walk","best_time":"Morning","skip_if":"Heavy rain","cost_estimate":"Minimal","saving_vs_standard":"N/A"}
        yield f"data: {json.dumps({'type':'agent_done','agent':'Planner','step':2,'data':plan})}\n\n"

        # AGENT 3
        yield f"data: {json.dumps({'type':'agent_start','agent':'Forecaster','step':3,'msg':'📅 Agent 3/4 — Schedule Forecaster plotting upcoming tasks...'})}\n\n"
        try:
            raw = llm(AGENT_FORECASTER, f"Farm: {farm_ctx}\nAnalysis: {json.dumps(analysis)}\nToday: {json.dumps(plan)}", 0.3, 400)
            forecast = parse_json(raw)
            if not isinstance(forecast, list) or len(forecast) < 3:
                forecast = [{"day_offset":5,"action":"Irrigation check","reason":"Soil moisture assessment"},{"day_offset":12,"action":"Fertilizer top dressing","reason":"Next nutrient window"},{"day_offset":20,"action":"Pest & disease scout","reason":"Preventive action"}]
        except:
            forecast = [{"day_offset":5,"action":"Irrigation check","reason":"Water needs"},{"day_offset":12,"action":"Fertilizer dose","reason":"Nutrition"},{"day_offset":20,"action":"Pest scout","reason":"Prevention"}]
        yield f"data: {json.dumps({'type':'agent_done','agent':'Forecaster','step':3,'data':forecast})}\n\n"

        # AGENT 4
        yield f"data: {json.dumps({'type':'agent_start','agent':'Writer','step':4,'msg':'✍️ Agent 4/4 — Message Writer composing your daily briefing...'})}\n\n"
        writer_input = f"""Write the CropAutopilot message. Fill ALL placeholders with real data:

DAY={day} | REGION={region} | WEATHER={weather} | AREA={area} | STAGE={stage}

ANALYST: {json.dumps(analysis)}
PLANNER: {json.dumps(plan)}
FORECASTER: {json.dumps(forecast)}

Replace every {{placeholder}} with actual values. Output the message directly."""
        try:
            final_msg = llm(AGENT_WRITER, writer_input, 0.65, 900)
            yield f"data: {json.dumps({'type':'final','message':final_msg,'stage':stage,'progress':progress,'analysis':analysis,'plan':plan,'forecast':forecast})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type':'error','message':str(e)})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"}
    )

@app.route("/api/ask", methods=["POST"])
def ask():
    data = request.json
    q   = data.get("question", "")
    ctx = data.get("context", "")
    prompt = f"""CropAutopilot AI — expert Indian farm advisor.
Context: {ctx}
Question: {q}
Answer directly. Exact quantities (kg/acre, litres/bigha), rupee estimates. Max 200 words. Emojis. End with 💡 pro tip."""
    try:
        r = client.chat.completions.create(model=MODEL, messages=[{"role":"user","content":prompt}], temperature=0.5, max_tokens=500)
        return jsonify({"success":True,"answer":r.choices[0].message.content})
    except Exception as e:
        return jsonify({"success":False,"error":str(e)}), 500

@app.route("/api/schedule", methods=["POST"])
def schedule():
    data       = request.json
    crop       = data.get("crop","Wheat")
    total_days = int(data.get("total_days",120))
    region     = data.get("region","Punjab")
    prompt = f"""Season schedule for {crop} in {region}, India. Duration: {total_days} days.
Return ONLY JSON array (no markdown, no preamble):
[{{"day":1,"action":"...","input":"...","quantity":"...","cost_saving":"₹..."}}]
12-15 milestones. Indian product names and exact quantities."""
    try:
        r = client.chat.completions.create(model=MODEL, messages=[{"role":"user","content":prompt}], temperature=0.3, max_tokens=1800)
        raw = r.choices[0].message.content
        m = re.search(r'\[.*\]', raw, re.DOTALL)
        sched = json.loads(m.group()) if m else []
        return jsonify({"success":True,"schedule":sched,"crop":crop})
    except Exception as e:
        return jsonify({"success":False,"error":str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True, port=5000)
