"""
Agent layer — real Agent Development Kit (ADK) version.

pip install google-adk

Replaces modules/agent.py's plain genai call + CSV log with:
  - An ADK `Agent` (Gemini-backed) with a real `tool` function the model
    can call to dispatch the notification - this is genuine tool-use
    agentic behavior, not just a templated prompt.
  - BigQuery as the audit-trail / notification sink (swap the tool body
    for a Cloud Function call to Twilio/SendGrid/Pub-Sub for a real SMS).
"""

import os
import json
from datetime import datetime, timezone

from modules.bq_client import insert_notification, PROJECT_ID


os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "True")
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", PROJECT_ID)
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", os.environ.get("GCP_REGION", "asia-south1"))

from google.adk.agents import Agent

MODEL_NAME = "gemini-2.5-flash"


def dispatch_notification(priority: str, alert_summary: str, recommendation: str,
                           district: str, block: str, village: str, source_anomaly: str) -> str:
    """Tool: writes an alert to the notifications_log BigQuery table and
    (in production) would trigger a Cloud Function to actually send an
    SMS/email to the district health officer. The agent calls this itself
    once it has decided the case merits notifying someone."""
    insert_notification({
        "created_at": datetime.now(timezone.utc).isoformat(),
        "priority": priority,
        "alert_summary": alert_summary,
        "recommendation": recommendation,
        "district": district,
        "block": block,
        "village": village,
        "source_anomaly": source_anomaly,
    })
    return f"Notification logged and (in prod) dispatched to {district}/{block} officer."


recommendation_agent = Agent(
    name="swasthya_recommendation_agent",
    model=MODEL_NAME,
    description="Turns a detected health-surveillance anomaly into a prioritized, explainable recommendation and dispatches a notification.",
    instruction="""You are a public health decision-support agent. You will be given
details of a statistically detected anomaly (a disease case spike or an
immunization coverage drop-off).

Steps:
1. Write a one-line plain-language alert summary suitable for an SMS to a
   district health officer.
2. Write a concrete, specific recommended action (2-3 sentences).
3. Assign a priority: LOW, MEDIUM, or HIGH.
4. Call the `dispatch_notification` tool with these values to log/send the alert.
5. Reply to the user with a short confirmation including the priority and
   the recommendation text.

Always ground the recommendation in the specific numbers given (village,
disease/vaccine, magnitude of deviation) - never give a generic answer.""",
    tools=[dispatch_notification],
)


def run_agent_on_anomaly(anomaly_details: dict) -> str:
    """Convenience wrapper for app.py: sends the anomaly JSON to the agent
    and returns its final text response. In a full ADK deployment you'd
    run this through a Runner/Session (see ADK docs) or as a hosted Vertex
    AI Agent Engine; this direct-call form works fine for the Streamlit demo."""
    from google.adk.runners import InMemoryRunner
    from google.genai import types

    runner = InMemoryRunner(agent=recommendation_agent, app_name="swasthya_ai")
    session = runner.session_service.create_session_sync(
        app_name="swasthya_ai", user_id="demo_user"
    )
    message = types.Content(
        role="user",
        parts=[types.Part(text=f"Anomaly details:\n{json.dumps(anomaly_details, indent=2)}")],
    )

    final_text = ""
    tool_was_called = False

    for event in runner.run(user_id="demo_user", session_id=session.id, new_message=message):
        # Detect whether dispatch_notification actually fired, so we don't
        # have to guess from the model's prose whether the BigQuery write happened.
        if event.content and event.content.parts:
            for part in event.content.parts:
                if getattr(part, "function_call", None) is not None:
                    tool_was_called = True
        if event.is_final_response() and event.content and event.content.parts:
            final_text = event.content.parts[0].text

    if not final_text:
        return ("⚠️ The agent returned no response. This usually means the "
                "underlying Gemini call failed (check GOOGLE_GENAI_USE_VERTEXAI, "
                "GOOGLE_CLOUD_PROJECT, and GOOGLE_CLOUD_LOCATION env vars, and that "
                "the service account/user has roles/aiplatform.user).")

    status_line = ("✅ Tool call confirmed — a row should now be in `notifications_log`."
                   if tool_was_called else
                   "⚠️ The agent responded but did NOT call dispatch_notification — "
                   "no BigQuery row was written this run.")
    return f"{final_text}\n\n---\n{status_line}"
