from pydantic import BaseModel, Field, ConfigDict
from typing import Optional

class File(BaseModel):
    path: str = Field(description= " The file path where the file will be created, e.g. 'src/components/Header.js'")
    purpose: str = Field(description="The purpose of the file, e.g. 'main application logic', 'data processing module', 'UI component for user profile display' etc.")
    
    
class Plan(BaseModel):
    name: str = Field(description="The name of the app to build")
    description: str = Field(description="A one line description of the app to be built, e.g.  ' A web application for managing personal finances.'")
    techstack: str = Field(description="The tech stack to be used to build the app, e.g. 'React, Node.js, PostgreSQL'")
    features: list[str]= Field(description="A list of features to be implemented in the app , e.g. ['User authentication', 'Data visualization dashboard', 'Real-time notifications']")
    files: list[File]= Field(description="A list of files to be created for the app, each with a 'path' and 'purpose'")


class ImplementationTask(BaseModel):
    file_path: str = Field(description="The file path where the implementation task will be carried out, e.g. 'src/components/Header.js'")
    task_description: str = Field(description="A detailed description of the implementation task, specifying what needs to be done, including variable names, function signatures, component details, and integration points with other tasks.")

class TaskPlan(BaseModel):
    implimentation_steps: list[ImplementationTask]= Field(description="A list of implementation tasks to be carried out for the project")
    model_config= ConfigDict(extra="allow")

class CoderState(BaseModel):
    task_plan: TaskPlan= Field(description="The plann for the task to be implemented")
    current_step_index: int= Field(description="The index of the current implementation step being worked on")
    current_file_content: Optional[str]= Field(None, description="The existing content of the file being modified")
