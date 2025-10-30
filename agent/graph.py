from dotenv import load_dotenv
load_dotenv()
from langchain_groq import ChatGroq
from langchain_google_genai import ChatGoogleGenerativeAI
from pprint import pprint
from .prompts import *
from .states import *
from .tools import *
from langgraph.constants import END
from langgraph.graph import StateGraph
from langchain.agents import create_agent
import json
import re

user_prompt= "I want to build a simple calculator web application."
 
# llm= ChatGroq(model= "openai/gpt-oss-120b")
llm= ChatGoogleGenerativeAI(model= "gemini-2.5-flash-lite", temperature= 0)
def _extract_json(text: str) -> str:
    # pull first top-level JSON object
    m = re.search(r"\{[\s\S]*\}", text)
    return m.group(0) if m else text


def planner_agent(state: dict)-> dict:
    user_prompt= state["user_prompt"]
    msg = llm.invoke(planner_prompt(user_prompt))
    raw = getattr(msg, "content", str(msg))
    data = json.loads(_extract_json(raw))
    resp = Plan.model_validate(data)
    return { "plan": resp}

def architect_agent(state: dict)-> dict:
    plan= state["plan"]
    plan_json = plan.model_dump_json()
    msg = llm.invoke(architect_prompt(plan_json))
    raw = getattr(msg, "content", str(msg))
    data = json.loads(_extract_json(raw))
    tp = TaskPlan.model_validate(data)
    tp.plan = plan
    return { "task_plan": tp}


def coder_agent(state: dict) -> dict:
    """LangGraph tool-using coder agent."""
    # Route tool calls to the session-specific temp directory if provided
    sid = state.get("session_id")
    if sid:
        try:
            set_default_session_id(sid)
            init_project_root(sid)
        except Exception:
            pass
    coder_state= state.get("coder_state")
    if coder_state is None:
        coder_state= CoderState(task_plan= state["task_plan"], current_step_index= 0)

    steps= coder_state.task_plan.implimentation_steps

    if coder_state.current_step_index >= len(steps):
        return {"coder_state": coder_state, "status": "DONE"}

    current_task= steps[coder_state.current_step_index]
    existing_content= read_file.run(current_task.file_path)
    user_prompt= (
        f"Task: {current_task.task_description}\n"
        f"File to modify: {current_task.file_path}\n"
        f"Existing file content:\n{existing_content}\n"
        "Use write_file(path, content) tp save your changes."
    )
    user_prompt= (
        f"Task: {current_task.task_description}\n"
        f"File to modify: {current_task.file_path}\n"
        f"Existing file content:\n{existing_content}\n"
        "Use write_file(path, content) tp save your changes."
    )
    system_prompt= coder_system_prompt()
    # resp= llm.invoke(system_prompt= system_prompt, user_prompt= user_prompt)
    coder_tools= [
        read_file,
        write_file,
        list_files,
        get_current_directory,
        run_cmd,
        # compatibility tool names expected by some models
        repo_browser_read_file,
        repo_browser_write_file,
        repo_browser_list_files,
        repo_browser_get_current_directory,
        repo_browser_run_cmd,
        repo_browser_print_tree,
    ]

    react_agent= create_agent(llm, coder_tools)

    react_agent.invoke({"messages": [{"role": "system", "content": system_prompt},
     {"role": "user", "content": user_prompt}],
     "tools": coder_tools})
    
    coder_state.current_step_index += 1
    return{"coder_state": coder_state}


graph= StateGraph(dict)
graph.add_node("planner", planner_agent)
graph.add_node("architect", architect_agent)
graph.add_node("coder", coder_agent)

graph.add_edge("planner", "architect")
graph.add_edge("architect", "coder")
graph.add_conditional_edges(
    "coder",
    lambda s: "END" if s.get("status") == "DONE" else "coder", {"END": END, "coder": "coder"}
)

graph.set_entry_point("planner")

agent= graph.compile()



if __name__ == "__main__":

    user_prompt= "create a simple calculator web application"

    result= agent.invoke( {"user_prompt": user_prompt}, {"recursion_limit": 100} )

    print(result)