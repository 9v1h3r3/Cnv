from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import json
import os
import time
from threading import Thread
from queue import Queue
import logging

app = Flask(__name__)
CORS(app)  # Enable CORS for frontend

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Store active jobs
active_jobs = {}

class MessengerJob:
    def __init__(self, job_id, token, user_id, messages, delay):
        self.job_id = job_id
        self.token = token
        self.user_id = user_id
        self.messages = messages
        self.delay = delay
        self.status = "running"
        self.progress = 0
        self.total = len(messages)
        self.logs = []
        self.thread = None
        
    def start(self):
        self.thread = Thread(target=self.run)
        self.thread.start()
        
    def run(self):
        try:
            for i, message in enumerate(self.messages):
                if self.status == "stopped":
                    break
                    
                # Update progress
                self.progress = i + 1
                
                # Send message
                success, log = self.send_message(message)
                self.logs.append(log)
                
                # Delay between messages
                if i < len(self.messages) - 1 and self.status == "running":
                    time.sleep(self.delay)
            
            if self.status == "running":
                self.status = "completed"
                self.logs.append(f"[{time.strftime('%H:%M:%S')}] ✅ All messages sent successfully!")
            else:
                self.logs.append(f"[{time.strftime('%H:%M:%S')}] ⏸ Stopped by user")
                
        except Exception as e:
            self.status = "error"
            self.logs.append(f"[{time.strftime('%H:%M:%S')}] ❌ Error: {str(e)}")
            
    def send_message(self, message):
        url = f"https://graph.facebook.com/v19.0/me/messages"
        
        payload = {
            "recipient": {"id": self.user_id},
            "message": {"text": message},
            "messaging_type": "RESPONSE"
        }
        
        params = {"access_token": self.token}
        
        try:
            response = requests.post(url, json=payload, params=params, timeout=30)
            response.raise_for_status()
            
            result = response.json()
            
            if "error" in result:
                log_msg = f"[{time.strftime('%H:%M:%S')}] ❌ Error: {result['error']['message']}"
                return False, log_msg
            else:
                log_msg = f"[{time.strftime('%H:%M:%S')}] ✅ Sent: {message[:30]}..."
                return True, log_msg
                
        except requests.exceptions.RequestException as e:
            log_msg = f"[{time.strftime('%H:%M:%S')}] ❌ Network error: {str(e)}"
            return False, log_msg
            
    def stop(self):
        self.status = "stopped"

@app.route('/api/send-message', methods=['POST'])
def send_single_message():
    """Send single message API"""
    data = request.json
    
    token = data.get('token')
    user_id = data.get('user_id')
    message = data.get('message')
    
    if not all([token, user_id, message]):
        return jsonify({"error": "Missing required parameters"}), 400
    
    url = f"https://graph.facebook.com/v19.0/me/messages"
    
    payload = {
        "recipient": {"id": user_id},
        "message": {"text": message},
        "messaging_type": "RESPONSE"
    }
    
    params = {"access_token": token}
    
    try:
        response = requests.post(url, json=payload, params=params)
        result = response.json()
        
        if response.status_code == 200:
            return jsonify({
                "success": True,
                "message_id": result.get("message_id"),
                "recipient_id": result.get("recipient_id")
            })
        else:
            return jsonify({
                "success": False,
                "error": result.get("error", {}).get("message", "Unknown error")
            }), 400
            
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/start-batch', methods=['POST'])
def start_batch():
    """Start batch messaging job"""
    data = request.json
    
    token = data.get('token')
    user_id = data.get('user_id')
    messages = data.get('messages', [])
    delay = data.get('delay', 5)
    
    if not all([token, user_id]) or not messages:
        return jsonify({"error": "Missing required parameters"}), 400
    
    # Create job ID
    job_id = f"job_{int(time.time())}"
    
    # Create and start job
    job = MessengerJob(job_id, token, user_id, messages, delay)
    active_jobs[job_id] = job
    job.start()
    
    logger.info(f"Started batch job {job_id} with {len(messages)} messages")
    
    return jsonify({
        "success": True,
        "job_id": job_id,
        "total_messages": len(messages)
    })

@app.route('/api/job-status/<job_id>', methods=['GET'])
def get_job_status(job_id):
    """Get status of a job"""
    if job_id not in active_jobs:
        return jsonify({"error": "Job not found"}), 404
    
    job = active_jobs[job_id]
    
    return jsonify({
        "job_id": job.job_id,
        "status": job.status,
        "progress": job.progress,
        "total": job.total,
        "logs": job.logs[-10:]  # Last 10 logs
    })

@app.route('/api/stop-job/<job_id>', methods=['POST'])
def stop_job(job_id):
    """Stop a running job"""
    if job_id not in active_jobs:
        return jsonify({"error": "Job not found"}), 404
    
    job = active_jobs[job_id]
    job.stop()
    
    return jsonify({"success": True, "message": "Job stopped"})

@app.route('/api/verify-token', methods=['POST'])
def verify_token():
    """Verify Facebook token validity"""
    data = request.json
    token = data.get('token')
    
    if not token:
        return jsonify({"error": "Token required"}), 400
    
    try:
        # Debug token
        url = f"https://graph.facebook.com/debug_token"
        params = {
            "input_token": token,
            "access_token": f"{os.getenv('FB_APP_ID')}|{os.getenv('FB_APP_SECRET')}"
        }
        
        response = requests.get(url, params=params)
        result = response.json()
        
        if "data" in result:
            data = result["data"]
            return jsonify({
                "valid": data.get("is_valid", False),
                "user_id": data.get("user_id"),
                "app_id": data.get("app_id"),
                "expires_at": data.get("expires_at"),
                "scopes": data.get("scopes", [])
            })
        else:
            return jsonify({"valid": False, "error": "Invalid response"})
            
    except Exception as e:
        return jsonify({"valid": False, "error": str(e)})

if __name__ == '__main__':
    # Load environment variables
    from dotenv import load_dotenv
    load_dotenv()
    
    # Run server
    app.run(debug=True, port=5000)
