"""
Facebook Messenger API Server for Render Deployment
Author: Your Name
Version: 2.0.0
Updated for Flask 2.3+ compatibility
"""

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import requests
import os
import time
import uuid
import threading
import logging
from datetime import datetime
import json

# Initialize Flask app
app = Flask(__name__, static_folder='.')

# Configure CORS
CORS(app, resources={
    r"/api/*": {"origins": "*"},
    r"/static/*": {"origins": "*"}
})

# Configuration
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# In-memory storage for jobs
active_jobs = {}
completed_jobs = {}

class MessengerJob:
    """Background job for sending messages"""
    
    def __init__(self, job_id, token, user_id, messages, delay):
        self.job_id = job_id
        self.token = token
        self.user_id = user_id
        self.messages = messages
        self.delay = delay
        self.status = "pending"
        self.progress = 0
        self.total = len(messages)
        self.logs = []
        self.created_at = datetime.now().isoformat()
        self.started_at = None
        self.completed_at = None
        self.thread = None
        
    def start(self):
        """Start the job in a separate thread"""
        self.status = "running"
        self.started_at = datetime.now().isoformat()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()
        logger.info(f"Job {self.job_id} started with {self.total} messages")
        
    def run(self):
        """Main job execution"""
        try:
            for i, message in enumerate(self.messages):
                if self.status == "stopped":
                    break
                    
                # Update progress
                self.progress = i + 1
                
                # Send message
                success, log_msg = self.send_message(message)
                self.add_log(log_msg, "info" if success else "error")
                
                # Add delay between messages
                if i < len(self.messages) - 1 and self.status == "running":
                    time.sleep(self.delay)
            
            # Finalize job
            if self.status == "running":
                self.status = "completed"
                self.completed_at = datetime.now().isoformat()
                self.add_log("✅ All messages sent successfully!", "success")
                
                # Move to completed jobs
                completed_jobs[self.job_id] = self
                if self.job_id in active_jobs:
                    del active_jobs[self.job_id]
                    
        except Exception as e:
            self.status = "error"
            self.completed_at = datetime.now().isoformat()
            self.add_log(f"❌ Job failed: {str(e)}", "error")
            logger.error(f"Job {self.job_id} failed: {e}")
            
    def send_message(self, message):
        """Send single message via Facebook Graph API"""
        url = "https://graph.facebook.com/v19.0/me/messages"
        
        payload = {
            "recipient": {"id": self.user_id},
            "message": {"text": message},
            "messaging_type": "RESPONSE"
        }
        
        params = {"access_token": self.token}
        
        try:
            # Truncate long messages for display
            display_msg = message[:50] + "..." if len(message) > 50 else message
            
            response = requests.post(
                url, 
                json=payload, 
                params=params, 
                timeout=30,
                headers={"User-Agent": "Facebook-Messenger-Bot/1.0"}
            )
            
            if response.status_code == 200:
                result = response.json()
                if "message_id" in result:
                    return True, f"Sent: {display_msg}"
                else:
                    return False, f"API Error: {result.get('error', {}).get('message', 'Unknown')}"
            else:
                error_msg = response.json().get('error', {}).get('message', 'Unknown error')
                return False, f"HTTP {response.status_code}: {error_msg}"
                
        except requests.exceptions.RequestException as e:
            return False, f"Network error: {str(e)}"
            
    def add_log(self, message, level="info"):
        """Add log entry with timestamp"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_entry = f"[{timestamp}] {message}"
        self.logs.append({"message": log_entry, "level": level, "time": timestamp})
        
        # Keep only last 100 logs
        if len(self.logs) > 100:
            self.logs = self.logs[-100:]
            
    def stop(self):
        """Stop the job"""
        if self.status == "running":
            self.status = "stopped"
            self.completed_at = datetime.now().isoformat()
            self.add_log("⏸ Job stopped by user", "warning")
            return True
        return False
        
    def get_status(self):
        """Get job status for API response"""
        return {
            "job_id": self.job_id,
            "status": self.status,
            "progress": self.progress,
            "total": self.total,
            "percentage": round((self.progress / self.total * 100) if self.total > 0 else 0, 1),
            "logs": self.logs[-20:],
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at
        }

# Static file serving
@app.route('/')
def serve_index():
    """Serve the main HTML page"""
    return send_from_directory('.', 'index.html')

@app.route('/<path:path>')
def serve_static(path):
    """Serve static files"""
    return send_from_directory('.', path)

# API Routes
@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint for Render"""
    return jsonify({
        "status": "healthy",
        "service": "Facebook Messenger API",
        "timestamp": datetime.now().isoformat(),
        "version": "2.0.0",
        "environment": os.environ.get('RENDER', 'development'),
        "active_jobs": len(active_jobs),
        "completed_jobs": len(completed_jobs)
    })

@app.route('/api/send', methods=['POST'])
def send_message():
    """Send a single message"""
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({"error": "No data provided"}), 400
            
        token = data.get('token')
        user_id = data.get('user_id')
        message = data.get('message')
        
        # Validation
        if not token or not user_id or not message:
            return jsonify({"error": "Missing required fields: token, user_id, message"}), 400
            
        # Send message
        url = "https://graph.facebook.com/v19.0/me/messages"
        payload = {
            "recipient": {"id": user_id},
            "message": {"text": message},
            "messaging_type": "RESPONSE"
        }
        
        response = requests.post(url, json=payload, params={"access_token": token})
        
        if response.status_code == 200:
            result = response.json()
            return jsonify({
                "success": True,
                "message_id": result.get("message_id"),
                "recipient_id": result.get("recipient_id")
            })
        else:
            error = response.json().get('error', {})
            return jsonify({
                "success": False,
                "error": error.get('message', 'Unknown error'),
                "code": error.get('code')
            }), 400
            
    except Exception as e:
        logger.error(f"Send message error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/batch/start', methods=['POST'])
def start_batch():
    """Start a batch messaging job"""
    try:
        data = request.get_json()
        
        token = data.get('token')
        user_id = data.get('user_id')
        messages = data.get('messages', [])
        delay = data.get('delay', 5)
        
        # Validation
        if not token or not user_id:
            return jsonify({"error": "Token and user_id are required"}), 400
            
        if not messages or not isinstance(messages, list):
            return jsonify({"error": "Messages must be a non-empty array"}), 400
            
        if delay < 1 or delay > 60:
            return jsonify({"error": "Delay must be between 1 and 60 seconds"}), 400
            
        # Create job
        job_id = f"job_{uuid.uuid4().hex[:8]}"
        job = MessengerJob(job_id, token, user_id, messages, delay)
        
        # Store job
        active_jobs[job_id] = job
        
        # Start job
        job.start()
        
        logger.info(f"Batch job {job_id} created for user {user_id} with {len(messages)} messages")
        
        return jsonify({
            "success": True,
            "job_id": job_id,
            "total_messages": len(messages),
            "estimated_time": len(messages) * delay,
            "message": "Batch job started successfully"
        })
        
    except Exception as e:
        logger.error(f"Start batch error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/batch/status/<job_id>', methods=['GET'])
def get_batch_status(job_id):
    """Get status of a batch job"""
    # Check active jobs
    if job_id in active_jobs:
        job = active_jobs[job_id]
        return jsonify(job.get_status())
    
    # Check completed jobs
    if job_id in completed_jobs:
        job = completed_jobs[job_id]
        return jsonify(job.get_status())
    
    return jsonify({"error": "Job not found"}), 404

@app.route('/api/batch/stop/<job_id>', methods=['POST'])
def stop_batch(job_id):
    """Stop a batch job"""
    if job_id in active_jobs:
        job = active_jobs[job_id]
        if job.stop():
            return jsonify({"success": True, "message": "Job stopped successfully"})
        else:
            return jsonify({"success": False, "message": "Job is not running"})
    
    return jsonify({"error": "Job not found or already completed"}), 404

@app.route('/api/verify', methods=['POST'])
def verify_token():
    """Verify Facebook token"""
    try:
        data = request.get_json()
        token = data.get('token')
        
        if not token:
            return jsonify({"error": "Token is required"}), 400
            
        # Get app credentials from environment
        app_id = os.environ.get('FB_APP_ID')
        app_secret = os.environ.get('FB_APP_SECRET')
        
        if not app_id or not app_secret:
            return jsonify({"error": "Server configuration error"}), 500
            
        # Debug token with Facebook
        debug_url = "https://graph.facebook.com/debug_token"
        params = {
            "input_token": token,
            "access_token": f"{app_id}|{app_secret}"
        }
        
        response = requests.get(debug_url, params=params, timeout=10)
        
        if response.status_code == 200:
            result = response.json()
            data = result.get('data', {})
            
            return jsonify({
                "valid": data.get('is_valid', False),
                "user_id": data.get('user_id'),
                "app_id": data.get('app_id'),
                "expires_at": data.get('expires_at'),
                "scopes": data.get('scopes', []),
                "issued_at": data.get('issued_at')
            })
        else:
            return jsonify({
                "valid": False,
                "error": "Failed to verify token"
            }), 400
            
    except Exception as e:
        logger.error(f"Token verification error: {e}")
        return jsonify({"valid": False, "error": str(e)}), 500

@app.route('/api/parse-file', methods=['POST'])
def parse_message_file():
    """Parse uploaded message file"""
    try:
        if 'file' not in request.files:
            return jsonify({"error": "No file uploaded"}), 400
            
        file = request.files['file']
        
        if file.filename == '':
            return jsonify({"error": "No file selected"}), 400
            
        # Check file extension
        if not file.filename.endswith('.txt'):
            return jsonify({"error": "Only .txt files are allowed"}), 400
            
        # Read and parse file
        content = file.read().decode('utf-8')
        messages = [
            line.strip() 
            for line in content.split('\n') 
            if line.strip() and not line.strip().startswith('#')
        ]
        
        return jsonify({
            "success": True,
            "filename": file.filename,
            "total_lines": len(messages),
            "messages": messages[:1000],
            "message": f"Parsed {len(messages)} messages successfully"
        })
        
    except Exception as e:
        logger.error(f"File parse error: {e}")
        return jsonify({"error": str(e)}), 500

# Cleanup old jobs
def cleanup_old_jobs():
    """Remove old completed jobs"""
    current_time = datetime.now()
    jobs_to_remove = []
    
    for job_id, job in completed_jobs.items():
        if job.completed_at:
            completed_time = datetime.fromisoformat(job.completed_at)
            if (current_time - completed_time).total_seconds() > 3600:  # 1 hour
                jobs_to_remove.append(job_id)
    
    for job_id in jobs_to_remove:
        del completed_jobs[job_id]
        logger.info(f"Cleaned up old job: {job_id}")

# Schedule cleanup
def schedule_cleanup():
    """Schedule periodic cleanup"""
    def cleanup_task():
        while True:
            time.sleep(300)  # Run every 5 minutes
            cleanup_old_jobs()
    
    thread = threading.Thread(target=cleanup_task, daemon=True)
    thread.start()

# Error handlers
@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Endpoint not found"}), 404

@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal server error: {error}")
    return jsonify({"error": "Internal server error"}), 500

@app.errorhandler(413)
def too_large(error):
    return jsonify({"error": "File too large. Maximum size is 16MB"}), 413

# Initialize cleanup on startup
schedule_cleanup()
logger.info("Facebook Messenger API started successfully")

# Main entry point
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') == 'development'
    
    logger.info(f"Starting server on port {port} (debug: {debug})")
    app.run(host='0.0.0.0', port=port, debug=debug)
