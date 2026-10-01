# Story design

How Lou's adventures are meant to read, and the rules the plan evaluator holds every story to.

## Principles

1. The first reply is a briefing: who the user is, who their handler is and how the handler reaches them, what is at stake, and how a stop works.
2. Characters are introduced before they act, every stop names who appears in it, and nobody shows up from nowhere.
3. Every stop earns a clue in words (a name, an alias, a place, a time), later steps use it, and the finale is solved from the clues the user gathered.
4. Every stop ties to the theme through sourced facts told in the story's voice; when research finds no real connection, the briefing says so instead of inventing one.
5. Codes, keys, and passwords come only from puzzles the user solves, and no puzzle gives away its own answer.
6. Real people are history, never characters: anyone in the research or the user's request appears only as sourced fact, never as a voice, a suspect, or someone who "sounds like" them.
7. The story fills the time the user gives: at least 2 stops from about 20 minutes, 3 from an hour, and 4 from two hours, unless the user asked for fewer.

## How a run plays

1. Opening. Lou gives the briefing, the number of stops, about how long it takes, and the first stop, then waits for "ready" before giving directions.
2. At a stop. The handler makes contact through their channel, says why this place matters to the mission using the stop's sourced fact ("Decca cut 'Rock Around the Clock' upstairs in 1954, and our thief knew it"), and sets the puzzle. At the first stop the user visits, the handler also restates the mission in a sentence.
3. An answer. Lou judges it generously, names the clue plainly ("the thief signs as Roxy, so hold on to that"), says where it points, and gives directions to the next stop. A wrong answer gets the next hint, never the answer.
4. Changes. A skipped stop's clue is still told, as a short scene. A new time limit is checked against the rest of the route, and a change of stops before the start means a new plan with a rewritten story.
5. Finale. The characters bring the clues together, the solution follows from them, and Lou closes with a case file of the stops, the real facts learned, the clues, and any camera photos.

## Where this lives

The plan evaluator enforces these rules; its checks are listed in [TOOLS.md](TOOLS.md). The instructions that make the agent follow them are in `agent.py`.
