from dotenv import load_dotenv
load_dotenv()
from langchain_groq import ChatGroq
from pprint import pprint
from prompts import *
from states import *
from tools import *
from langgraph.constants import END
from langgraph.graph import StateGraph
from langchain.agents import create_agent

user_prompt= "I want to build a simple calculator web application."
 
llm= ChatGroq(model= "openai/gpt-oss-120b")

def planner_agent(state: dict)-> dict:
    user_prompt= state["user_prompt"]
    resp= llm.with_structured_output(Plan).invoke(planner_prompt(user_prompt))
    return { "plan": resp}

def architect_agent(state: dict)-> dict:
    plan= state["plan"]
    resp= llm.with_structured_output(TaskPlan).invoke(architect_prompt(plan))
    if resp is None:
        raise ValueError("Architect agent returned None")
    
    resp.plan= plan
    return { "task_plan": resp}


def coder_agent(state: dict) -> dict:
    """LangGraph tool-using coder agent."""
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
    coder_tools= [read_file, write_file, list_files, get_current_directory, run_cmd]

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