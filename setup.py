from setuptools import find_packages, setup


setup(
    name="angl",
    version="0.1.12",
    description="Contract-checked code regeneration from Angl behavior chapters",
    long_description=open("README.md", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    author="David Gao",
    python_requires=">=3.9",
    packages=find_packages(include=["angl", "angl.*"]),
    entry_points={
        "console_scripts": [
            "angl=angl.cli:main",
        ],
    },
)
