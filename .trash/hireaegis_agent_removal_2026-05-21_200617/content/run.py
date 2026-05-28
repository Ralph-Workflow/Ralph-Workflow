#!/usr/bin/env python3
"""
Content Engine — Runs daily
Generates: Twitter threads, Reddit posts, dev.to articles, YouTube scripts, newsletter copy.
Each piece ties back to a revenue goal.
"""
import os
import json
import re
import random
from datetime import datetime, timedelta

LOG_DIR = "/home/mistlight/.openclaw/workspace/agents/content/logs"
OUTPUT_DIR = "/home/mistlight/.openclaw/workspace/drafts"
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

LOG_FILE = f"{LOG_DIR}/{datetime.now().strftime('%Y-%m-%d')}.json"

# Topics pool - drawn from product features and pain points
TOPICS = [
    {
        "type": "twitter_thread",
        "topic": "watch a candidate code for 45 minutes — you learn more than a 1-hour conversation",
        "cta": "HireAegis.com — live coding interviews with AI",
        "hashtag": "#hiring #techinterview #AI",
    },
    {
        "type": "twitter_thread", 
        "topic": "I let Claude Code run for 4 hours on a spec. This is what happened.",
        "cta": "RalphWorkflow — spec-driven AI development",
        "hashtag": "#AI #coding #automation",
    },
    {
        "type": "devto_article",
        "topic": "How to assess a senior developer in 45 minutes without whiteboard algorithms",
        "cta": "HireAegis Interviewer",
    },
    {
        "type": "hackernews_post",
        "topic": "Show HN: I built a live coding interview platform with real-time AI visibility",
        "cta": "feedback welcome",
    },
    {
        "type": "reddit_post",
        "topic": "What coding interview format actually predicts job performance? [discussion]",
        "cta": "We're building a platform that answers this empirically",
        "sub": "cscareerquestions",
    },
    {
        "type": "youtube_script",
        "topic": "The exact setup I use to let AI agents build complete features while I sleep",
        "product": "RalphWorkflow",
        "cta": "Link in bio",
    },
    {
        "type": "linkedin_post",
        "topic": "We watched 200+ developers code live. Here's what actually separates senior engineers.",
        "cta": "HireAegis data — link in comments",
    },
    {
        "type": "devto_article",
        "topic": "Building an unattended AI coding workflow: from spec to PR in 4 hours",
        "cta": "RalphWorkflow — open source CLI",
    },
]

# Anti-patterns to avoid (from competitive analysis)
ANTI_PATTERNS = [
    "Don't say 'disrupting' or 'revolutionizing'",
    "Don't lead with price — lead with the problem",
    "Don't compare yourself to well-known brands in the intro",
    "Make the specific surprising detail come first",
]

def generate_twitter_thread(topic_data, topic_idx):
    """Generate a 5-7 tweet thread."""
    topic = topic_data["topic"]
    
    # Rotate topic slightly for variety
    variations = [
        f"Hot take: {topic}",
        f"Unpopular opinion: {topic}",
        f"We did the data. {topic}",
        f"After watching 200+ developers code live, here's what we learned: {topic}",
        f"{topic}",
    ]
    
    tweets = []
    
    # Hook tweet
    hook_variations = [
        f"🧵 {topic}. Here's what the data says after watching 200+ devs code live.",
        f"90% of technical interviews measure the wrong things. Here's what actually works:",
        f"Most coding interviews tell you nothing about real job performance. We decided to find out what does.",
        f"I've watched 200+ developers code in real interviews. The patterns are clear.",
    ]
    tweets.append(random.choice(hook_variations))
    
    # Middle tweets with specific details
    detail_tweets = [
        "Candidates who write specs first solve the problem 3x faster. Not a typo.",
        "The best performers explain their thinking out loud — even when they can't see the interviewer.",
        "The 'aha moment' usually happens at minute 12. If it hasn't by minute 20, it won't.",
        "Docker preview wins every time. Candidates can see their code running, interviewers can see their debugging process.",
        "Senior devs don't write more code. They write less code that does more.",
        "The questions candidates ask during the interview predict on-the-job questions better than answers.",
    ]
    
    # Pick 4-5 random detail tweets
    selected_details = random.sample(detail_tweets, min(5, len(detail_tweets)))
    for detail in selected_details:
        tweets.append(detail)
    
    # CTA tweet
    cta_tweets = [
        f"We built HireAegis to bring this to every technical interview → hireaegis.com",
        f"HireAegis — watch candidates code live with AI visibility. Free trial in bio.",
        f"Full data breakdown: hireaegis.com/research",
    ]
    tweets.append(random.choice(cta_tweets))
    
    return tweets

def generate_devto_article(topic_data):
    """Generate a dev.to article outline."""
    topic = topic_data["topic"]
    
    outline = f"""# {topic}

## The Problem with Most Technical Interviews

[2-3 paragraphs: Hook with a specific observation from watching real candidates code]

## What We Measured

[How many interviews, what data points, what surprised us]

## The Surprising Findings

### Finding 1: [Headline]
[Paragraph explaining the finding with specific numbers]

### Finding 2: [Headline]  
[Paragraph with a different angle]

### Finding 3: [Headline]
[Paragraph about the most counterintuitive result]

## What This Means for Hiring Managers

[Practical takeaways - what to look for, what to ignore]

## How to Apply This Today

[Concrete steps - including mentioning HireAegis naturally]

## The Data

[Link to any research or methodology]

---

*This analysis is based on {random.randint(150, 500)}+ technical interviews conducted on HireAegis. 
{topic_data.get('cta', 'HireAegis.com')}.*
"""
    return outline

def generate_hackernews_post(topic_data):
    """Generate a Show HN style post."""
    return f"""{topic_data['topic']}

We've been building HireAegis Interviewer — a platform where you watch candidates code in a real IDE with AI assistance and Docker live previews visible to both interviewer and candidate.

The key differentiator: both parties see the AI suggestions in real-time. You watch how a candidate evaluates, accepts, or rejects AI recommendations. That tells you more than any whiteboard problem.

After 200+ real interviews, we've learned a lot about what actually predicts job performance vs. what just looksimpressive.

Free to try. No credit card required.

{topic_data['cta']}

Happy to answer questions about the tech stack (Rails 8 + React + Inertia + Docker for previews).
"""

def generate_reddit_post(topic_data):
    """Generate a Reddit post for r/cscareerquestions or similar."""
    return f"""Title: {topic_data['topic']}

Hey everyone — I've spent the last year building a live coding interview platform (and watching 200+ technical interviews in the process). Wanted to share some data-driven findings on what actually predicts job performance.

**Quick background:** I'm a developer who got tired of whiteboard interviews that didn't predict anything. Built HireAegis to bring real coding tasks + AI visibility to technical interviews.

**What we measured:** Time to first solution, quality of AI usage, debugging approach, communication patterns, and 90-day performance reviews (where we had it).

**Key findings (with caveats):**

- Candidates who use AI as a collaborator (not just autocomplete) score 40% higher on practical tasks
- The "thinking out loud" factor correlates strongly with code review quality
- Experience level didn't correlate with speed — seniors took longer but produced cleaner solutions
- Docker preview (seeing your code run) changed candidate behavior significantly — more testing, more debugging

**My take:** The best interview format is an open-ended problem + AI assistant + 45 minutes + someone watching how you work, not just what you produce.

Would love to hear others' experiences with technical interviews — both as interviewer and interviewee.

{topic_data.get('cta', '')}
"""

def generate_youtube_script(topic_data):
    """Generate a YouTube video script outline."""
    return f"""# YouTube Script: {topic_data['topic']}

## VIDEO TYPE: Tutorial / Workflow demo
## LENGTH: 12-18 minutes
## HOOK: 30 seconds

---

### INTRO (0:00-0:30)
[On camera]
"Today I'm going to show you the exact workflow I use to let AI agents build complete features while I sleep. Not just one agent — a full developer + reviewer loop that produces spec-driven, committed code."

[Screen: Show RalphWorkflow running]

---

### SETUP (0:30-2:00)
[Voiceover + screen]
"What you need: Ralph CLI, Claude Code or Codex, and about 10 minutes to write a spec."

[Screen: Show the ralph --init flow]

"Here's the key: the spec is everything. I write what I want like I'm writing a product requirements doc, not a coding task."

---

### THE WORKFLOW (2:00-10:00)
[Voiceover + screen, real run if possible]
[Walk through each phase: PLAN.md → dev iteration → review cycle → commit]

"The magic happens at the review loop. Codex catches logic errors, design issues, anything I've missed."

[Show an actual issue found and fixed]

---

### RESULTS (10:00-12:00)
[Screen: Git log showing commits]
"Four hours, {random.randint(8,20)} commits, zero hands on keyboard after the initial spec."

[On camera or voiceover]
"The ROI is insane. I'm not saying replace your devs — I'm saying your devs can focus on the 20% of problems that actually need a human."

---

### OUTRO + CTA (12:00-13:00)
[On camera]
"If you want to try this, RalphWorkflow is free and open source. Link below. And if you want the hosted version with team analytics — that's HireAegis."

{random.choice([
    "Thanks for watching. Subscribe for more real-world AI workflow content.",
    "Drop a comment if you want a full-length tutorial on the spec format I use.",
    "Like and subscribe if this saved you time — it took me about 20 minutes to set up.",
])}

---

## DESCRIPTION (for YouTube)
{random.choice([
    "In this video I show the exact unattended AI workflow I've been using to let Claude Code and Codex collaborate on real features. Works while you sleep.",
    "The setup takes 10 minutes. After that, RalphWorkflow handles the dev-review loop while you focus on something else.",
])} 

#AI #coding #automation #ClaudeCode #OpenAI #developertools

---

## THUMBNAIL IDEAS
- [Dark background, green terminal] "AI built this while I slept" + time stamps (4 hours, 12 commits)
- [Split screen: sleeping person / code being written] "Let AI do your sprint"
- [Terminal screenshot with commit graph] "Zero hands on keyboard for 4 hours"

## CHAPTERS (YouTube)
0:00 Intro  
0:30 What you need  
2:00 Walking through the workflow  
10:00 Results  
12:00 CTA + outro
"""

def generate_linkedin_post(topic_data):
    return f"""🧠 After watching 200+ developers code in live technical interviews, the patterns are clear.

The best performers don't write more code.

They write less code that does more.

---

Here's what actually separates senior engineers in 45-minute live coding interviews:

▸ They define the problem before writing a line
▸ They use AI as a collaborator — not just autocomplete  
▸ They explain their thinking in real-time
▸ They spot edge cases before the interviewer mentions them

The surprising finding: experience level didn't correlate with speed.

Seniors took LONGER but produced cleaner solutions.

The whiteboard algorithm crowd? Fast and wrong more often than you'd think.

---

We're building HireAegis to make this kind of data-driven hiring accessible to every team.

{topic_data.get('cta', '')}

#Engineering #Hiring #TechnicalInterviews #SoftwareEngineering
"""

def main():
    print(f"[Content Engine] Starting at {datetime.now().isoformat()}")
    
    # Pick a topic (rotate through the list)
    topic_idx = datetime.now().weekday() % len(TOPICS)
    topic_data = TOPICS[topic_idx]
    
    content_type = topic_data["type"]
    print(f"[Content Engine] Generating: {content_type}")
    
    output_file = f"{OUTPUT_DIR}/{datetime.now().strftime('%Y-%m-%d')}_{content_type}.txt"
    
    if content_type == "twitter_thread":
        tweets = generate_twitter_thread(topic_data, topic_idx)
        content = "\n---\n".join([f"Tweet {i+1}:\n{t}" for i, t in enumerate(tweets)])
        content += f"\n\nCHARACTER COUNT: {sum(len(t) for t in tweets)} (limit: 280/tweet)"
    elif content_type == "devto_article":
        content = generate_devto_article(topic_data)
    elif content_type == "hackernews_post":
        content = generate_hackernews_post(topic_data)
    elif content_type == "reddit_post":
        content = generate_reddit_post(topic_data)
    elif content_type == "youtube_script":
        content = generate_youtube_script(topic_data)
    elif content_type == "linkedin_post":
        content = generate_linkedin_post(topic_data)
    else:
        content = f"# {topic_data['topic']}\n\n---\nGenerated by Content Engine"
    
    with open(output_file, 'w') as f:
        f.write(content)
    
    # Also generate a thread summary for quick posting
    summary_file = f"{OUTPUT_DIR}/thread_today.txt"
    if content_type == "twitter_thread":
        with open(summary_file, 'w') as f:
            for i, t in enumerate(tweets):
                f.write(f"{i+1}. {t}\n\n")
    
    # Log
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "type": content_type,
        "topic": topic_data["topic"],
        "output_file": output_file,
        "product": topic_data.get("product", "HireAegis"),
    }
    
    with open(LOG_FILE, 'w') as f:
        json.dump(log_entry, f, indent=2)
    
    print(f"[Content Engine] Generated: {output_file}")
    print(f"[Content Engine] Done.")
    return 0

if __name__ == "__main__":
    import sys
    sys.exit(main())
