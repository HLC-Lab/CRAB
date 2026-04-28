from typing import List, Any  
  
class DataContainer:  
    """Holds runtime metrics for a specific application."""  
    def __init__(self, app_id: int, conv_goal: bool, label: str, unit: str, msg_size: int = 0):  
        self.app_id = app_id  
        self.conv_run = 0  
        self.label = label  
        self.unit = unit  
        self.conv_goal = conv_goal  
        self.converged = False  
        self.num_samples = []  
        self.data = []  
        self.msg_size = msg_size  
  
    def get_title(self) -> str:  
        return f"{self.app_id}_{self.label}_{self.unit}"  
  
    def md_to_list(self) -> List[Any]:  
        return [self.app_id, self.label, self.unit, self.conv_goal, self.converged, self.conv_run, self.msg_size] + self.num_samples
