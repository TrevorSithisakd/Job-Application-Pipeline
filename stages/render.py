from docx import Document
from schemas import ResumeDraft
from pathlib import Path
from paths import RESUMES_DIR

# function to build document and saves its file path
def render(draft: ResumeDraft, job_id:int, version):
    path = RESUMES_DIR / str(job_id) / f"v{version}.docx"
    path.parent.mkdir(parents=True, exist_ok=True)   # save writes a file, not folders
    new_doc = Document()
    tagline = new_doc.add_paragraph(draft.tagline)
    
    # education    
    new_doc.add_heading("Education", 1)
    new_doc.add_paragraph(f"{draft.education}")

    # how to build the file i would think it would have to be using the json format as a guide
    for project in draft.projects:
        new_doc.add_heading(project.title, 1)
        for bullet in project.bullets:
            new_doc.add_paragraph(bullet, style ="List Bullet")
    if draft.additional:
             new_doc.add_paragraph(draft.additional)
    
        

    for skill in draft.skills:
        new_doc.add_paragraph(f"{skill.label}: {skill.content}")

    for experience in draft.experience:
            new_doc.add_heading(f"{experience.role} | {experience.org} {experience.dates}", 1)
            for bullet in experience.bullets:
                new_doc.add_paragraph(bullet, style ="List Bullet")
            
    new_doc.save(path)
    return path

if __name__ == "__main__":
    text = Path("data/resumes/39/v2.json").read_text(encoding="utf-8")
    draft = ResumeDraft.model_validate_json(text)
    render(draft, 39, 2)