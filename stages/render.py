from docx import Document
from schemas import ResumeDraft

# function to build document and saves its file path
def render(draft: ResumeDraft, job_id:int, version):
    n = 0 # placeholder 
    new_doc = Document()
    tagline = new_doc.add_paragraph(draft.tagline)
    new_doc.save('text.doc')
    # how to build the file i would think it would have to be using the json format as a guide
    