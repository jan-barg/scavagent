# Supplied class assignment

The text below was provided by Jan on September 28, 2026. It is preserved as assignment reference, not as evidence that any requirement is already implemented. Lecture-specific guidance and the linked deployment tutorial were not attached.

Build a domain-specific agent that calls tools, and deploy it to Google Cloud Run.

What you're building
Think of a target user and problem for your agent to solve. There are not strict limitations on what this can be, I recommend you take inspiration from your own life.

Build a web-based chat agent that uses tools to assist the user. Use gemini-web-tool-calling.zip as a starting point.

Your agent must:

Remember the conversation across the session.
Have at least three tools.
At least one tool must make a request for external data such as an API or database.
Have original tools. You need one original tool per team member that no other team in the class built. We will compare every submission's tools against each other.
Follow the guidance on tool writing from the lecture: Name and describe your tools and their arguments well and gracefully handle errors relaying actionable information to the model.
Show its tool calls. Keep the starter's /chat response shape: response, session_id, and tool_calls with the name, args and result of every call. Showing tool calls in your UI is encouraged.
Have a frontend that's different from the base gemini-web-tool-calling.zip that makes it clear what the agent is and how to use it.
You may notice that the exact requirements are vague. This is by design. The most important part of this assignment is that you’re creative with it.

README
Each agent should come with a README.md that describes the project and lists three sample queries for the grader to test with.

How it's graded
Course Assistants will clone your repo and use your deployed agent in a browser.

Section

Points

What we assess

Basics

1

A README and a valid submission.json.

Functionality

11

Deployed and reachable. Clear purpose. Answers example queries correctly. Calls tools when it should. Follows the conversation and keeps sessions separate.

Tools

8

At least one tool uses external data. Tools are well written and called appropriately.

Creativity

5

At least three tools. Original tools no other team built. A frontend changed from the starter. The agent does something interesting.

Larger groups come with larger expectations. For this project, that means one original tool per team member.

How to submit
Submit your GitHub repo URL on Courseworks. One person submits per group.

Repo
At the root of your repo:
app.py, pyproject.toml and uv.lock
README.md
submission.json, listing every team member's Columbia UNI or email in authors, including your own:
{"deploy_url": "https://your-agent.example.run.app", "authors": ["abc1234", "xyz9876"]}
Private repos are fine. Just add these GitHub accounts as collaborators (Settings > Collaborators > Add people): codeboi07, bhuvighosh3, nniishhh, x.
Deployment
Deploy to Cloud Run with continuous deploy from GitHub, following Deploying to Cloud Run from GitHubLinks to an external site..
Keep it running until grades are released.
Use of Coding Agents
I encourage the use of coding agents but expect you to understand the code you submit and for it to reflect the style of agents that we've covered in the class so far.

Rubric
Peer Review Rubric (non-credit, peer style critique only) (1)
Peer Review Rubric (non-credit, peer style critique only) (1)
Criteria	Ratings	Pts
This criterion is linked to a Learning Outcome Experience & Usefulness
USE THE AGENT. As a user, did this agent actually work for you? Focus on your real experience, not what you imagine it could do with more polish.
5 pts
Wow
Immediately intuitive, and the results made me think or see something differently.
4 pts
Impressed
Smooth experience, results felt genuinely useful or interesting.
3 pts
Functional
Understood it, got it working, results were okay but not memorable.
2 pts
Bumpy
Got the general idea, but struggled to get useful results.
1 pts
Lost
Couldn't figure out what it does or how to use it.
5 pts
This criterion is linked to a Learning Outcome Risk-Taking & Ambition
READ THE CODE. Did the creator push beyond what was comfortable or expected? Ambitious failures count more than safe successes here.
5 pts
Full send
Went for something genuinely bold, even if parts are rough around the edges.
4 pts
Real swing
Took on something clearly difficult or unfamiliar and made real progress.
3 pts
Solid push
Tried something meaningfully outside their comfort zone, with mixed results.
2 pts
Toe in the water
One small stretch beyond the requirements, but mostly played it safe.
1 pts
Safe bet
Did exactly what the assignment asked, nothing more.
5 pts
This criterion is linked to a Learning Outcome Originality & Voice
Does this project feel like the creator made it their own?
5 pts
One of a kind
Feels like no one else in the class would have built this. The choices reflect real curiosity and a distinct perspective.
4 pts
Their own thing
Clear point of view. They explored the problem and made deliberate, personal choices.
3 pts
Getting there
Some creative choices hint at a perspective behind the project.
2 pts
Light touch
One or two personal choices, but mostly follows the obvious path.
1 pts
Off the shelf
Feels like a template. Could be anyone's project.
5 pts
