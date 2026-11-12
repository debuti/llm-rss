from importlib.resources import files


def load(name: str) -> str:
    return (files(__package__) / f"{name}.md").read_text(encoding="utf-8")


def render(template: str, **values: str) -> str:
    for k, v in values.items():
        template = template.replace("{{" + k + "}}", v)
    return template
