import os
import sys
import subprocess
import requests
import json
from datetime import datetime
from urllib.parse import urlparse

# ==============================================================================
# Load .env file
# ==============================================================================
def load_env(env_path=".env"):
    if not os.path.exists(env_path):
        print(f"Error: {env_path} file not found.")
        sys.exit(1)
    
    with open(env_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip()

# Load env variables from local directory
load_env(os.path.join(os.path.dirname(__file__), ".env"))

NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_URL = os.getenv("NOTION_URL")
NOTION_VERSION = os.getenv("NOTION_VERSION", "2022-06-28")

if not NOTION_TOKEN or not NOTION_URL:
    print("Error: NOTION_TOKEN or NOTION_URL is missing in the .env file.")
    sys.exit(1)

# Extract NOTION_DATABASE_ID from URL
# e.g., https://app.notion.com/p/342391eeae1b80bb9ecbf7c9a9ca71aa?v=...
# Path is /p/342391eeae1b80bb9ecbf7c9a9ca71aa
parsed_url = urlparse(NOTION_URL)
path_parts = [part for part in parsed_url.path.split("/") if part]
if not path_parts:
    print(f"Error: Could not parse Notion database ID from URL: {NOTION_URL}")
    sys.exit(1)

NOTION_DATABASE_ID = path_parts[-1]
print(f"Parsed Notion Database ID: {NOTION_DATABASE_ID}")

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": NOTION_VERSION
}

def get_pending_tasks():
    """Fetch tasks from the Notion database that are not yet started."""
    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"
    
    # Fetch tasks that are either 'To Do' or 'In Progress'
    payload = {
        "filter": {
            "or": [
                {
                    "property": "Status",
                    "select": {
                        "equals": "To Do"
                    }
                },
                {
                    "property": "Status",
                    "select": {
                        "equals": "In Progress"
                    }
                }
            ]
        }
    }
    
    try:
        response = requests.post(url, headers=HEADERS, json=payload)
        response.raise_for_status()
        return response.json().get("results", [])
    except requests.exceptions.RequestException as e:
        print(f"Error fetching tasks from Notion: {e}")
        return []

def update_task_status(page_id, new_status):
    """Update the status of a task in Notion so it isn't picked up again."""
    url = f"https://api.notion.com/v1/pages/{page_id}"
    payload = {
        "properties": {
            "Status": {
                "select": {
                    "name": new_status
                }
            }
        }
    }
    try:
        response = requests.patch(url, headers=HEADERS, json=payload)
        response.raise_for_status()
        print(f"Successfully updated task {page_id} to '{new_status}'")
    except requests.exceptions.RequestException as e:
        print(f"Error updating task status in Notion: {e}")

def get_page_content(page_id):
    """Fetch the text content of the Notion page."""
    url = f"https://api.notion.com/v1/blocks/{page_id}/children"
    try:
        response = requests.get(url, headers=HEADERS)
        response.raise_for_status()
        blocks = response.json().get("results", [])
        
        content = ""
        for block in blocks:
            block_type = block.get("type")
            if block_type and block_type in block:
                rich_texts = block[block_type].get("rich_text", [])
                for text in rich_texts:
                    content += text.get("plain_text", "") + "\n"
        return content
    except requests.exceptions.RequestException as e:
        print(f"Error fetching page content: {e}")
        return ""

def process_task(task):
    """Extract information from the task and execute agy."""
    page_id = task["id"]
    
    # Extract Title (Assuming the standard 'Name' property is used for title)
    properties = task.get("properties", {})
    title = "Untitled Task"
    for prop_name, prop_data in properties.items():
        if prop_data.get("type") == "title":
            title_array = prop_data.get("title", [])
            if title_array:
                title = title_array[0].get("plain_text", title)
            break
            
    # Fetch detailed task description from the page blocks
    description = get_page_content(page_id)
    
    print(f"\nProcessing Task: {title}")
    
    # Mark task as 'In Progress' so subsequent cron jobs don't re-run it
    update_task_status(page_id, "In Progress")

    # Ensure src directory exists
    src_dir = os.path.join(os.getcwd(), "src")
    if not os.path.exists(src_dir):
        os.makedirs(src_dir)

    # Construct the meta-prompt that includes the user's constraints
    meta_prompt = f"""
I have a new task for you, extracted from my Notion board.

TASK TITLE: {title}
TASK DESCRIPTION:
{description}

CRITICAL CONSTRAINTS & INSTRUCTIONS:
1. Subagents: Please create and coordinate with subagents/multiagents if necessary to accomplish this task efficiently.
2. Quota Management & Handoff: We have a limited token quota (5 hours or weekly limit). 
   - You MUST monitor the complexity and length of your progress.
   - If you anticipate approaching the token limit, or if the task is taking too many steps, STOP early.
   - Before stopping, you MUST leave a highly detailed documentation or markdown notes file (e.g., `progress_handoff.md`) detailing exactly what was accomplished, what is remaining, and explicit instructions on how to continue.
   - This handoff document is critical because the remainder of the task will be picked up by a local 'Hermes' agent running locally via LM Studio. Always leave enough quota to write this handoff document completely.
3. Workspace: All code, project files, and the handoff document MUST be generated in your current working directory (`{src_dir}`). Do NOT use a temporary or scratch directory.
"""

    print("Sending prompt to agy...")
    
    # Run agy using subprocess
    command = [
        "agy",
        "--add-dir", src_dir,
        "-p", meta_prompt, 
        "--dangerously-skip-permissions"
    ]
    
    try:
        result = subprocess.run(command, text=True)
        if result.returncode == 0:
            print(f"Task '{title}' processed successfully by agy.")
            # Mark task as 'In Review' after completing the task
            update_task_status(page_id, "In Review")
        else:
            print(f"agy process exited with error code {result.returncode}")
    except Exception as e:
        print(f"Error executing agy: {e}")

def main():
    print(f"[{datetime.now()}] Starting Notion to AGY automator...")
    tasks = get_pending_tasks()
    
    if not tasks:
        print("No pending tasks found.")
        return

    print(f"Found {len(tasks)} pending task(s).")
    
    # Process tasks one by one
    for task in tasks:
        process_task(task)

if __name__ == "__main__":
    main()
