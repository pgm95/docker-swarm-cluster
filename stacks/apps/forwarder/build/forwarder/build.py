#!/usr/bin/env python3
"""Build script to concatenate modules into single forwarder.py.
Runs inside the Docker build stage
"""

import re
from pathlib import Path

BUILD_DIR = Path(__file__).parent
PACKAGE_DIR = BUILD_DIR / 'forwarder'
OUTPUT_FILE = BUILD_DIR / 'forwarder.py'

# Module order (respects dependencies - bottom-up)
MODULES = [
    'types.py',
    'matching.py',
    'config.py',
    'connection.py',
    'parsing.py',
    'extraction.py',
    'forwarding.py',
    'processing.py',
    'runner.py',
]

# Templates to inline as constants
TEMPLATES = [
    ('extracted_code.html', 'TEMPLATE_CODE'),
    ('extracted_link.html', 'TEMPLATE_LINK'),
]


def remove_imports(content: str) -> tuple[str, set[str], set[str]]:
    """Remove import statements and return (cleaned_content, stdlib_imports, typing_imports)."""
    stdlib_imports: set[str] = set()
    typing_imports: set[str] = set()

    # Remove multi-line relative imports: from .module import (\n...\n)
    content = re.sub(r'^from \.[a-z_]+ import \([^)]+\)\n*', '', content, flags=re.MULTILINE)

    # Remove single-line relative imports at start of line: from .module import ...
    content = re.sub(r'^from \.[a-z_]+ import [^\n]+\n', '', content, flags=re.MULTILINE)

    # Remove indented relative imports (local imports inside functions) - replace with blank line to preserve structure
    content = re.sub(r'^(\s+)from \.[a-z_]+ import [^\n]+( +#[^\n]*)?\n', r'\1pass  # import removed\n', content, flags=re.MULTILINE)

    # Extract and remove 'import X' statements
    for match in re.finditer(r'^(import [a-z_]+)\n', content, re.MULTILINE):
        stdlib_imports.add(match.group(1))
    content = re.sub(r'^import [a-z_]+\n', '', content, flags=re.MULTILINE)

    # Extract and remove 'from X import Y' statements (non-relative)
    for match in re.finditer(r'^(from [a-z_.]+ import .+)\n', content, re.MULTILINE):
        line = match.group(1)
        if line.startswith('from typing import'):
            # Parse typing imports
            items = re.search(r'from typing import (.+)', line)
            if items:
                for item in items.group(1).split(','):
                    typing_imports.add(item.strip())
        else:
            stdlib_imports.add(line)
    content = re.sub(r'^from [a-z_.]+ import .+\n', '', content, flags=re.MULTILINE)

    return content, stdlib_imports, typing_imports


def remove_docstring(content: str) -> str:
    """Remove module-level docstring."""
    # Match docstring at start of file
    content = re.sub(r'^"""[^"]*"""\n*', '', content)
    content = re.sub(r"^'''[^']*'''\n*", '', content)
    return content


def build():
    """Build single-file forwarder.py from modules."""

    # Collect all unique imports
    all_stdlib_imports: set[str] = set()
    all_typing_imports: set[str] = set()

    # Collect module content (without imports)
    module_contents: list[str] = []

    # Inline templates as constants
    template_constants: list[str] = []
    for template_name, var_name in TEMPLATES:
        template_path = PACKAGE_DIR / 'templates' / template_name
        content = template_path.read_text()
        template_constants.append(f'{var_name} = """{content}"""')

    # Process each module
    for module_name in MODULES:
        module_path = PACKAGE_DIR / module_name
        content = module_path.read_text()

        # Remove docstring and imports
        content = remove_docstring(content)
        content, stdlib_imports, typing_imports = remove_imports(content)

        all_stdlib_imports.update(stdlib_imports)
        all_typing_imports.update(typing_imports)

        # Clean up multiple blank lines
        content = re.sub(r'\n{3,}', '\n\n', content)
        content = content.strip()

        if content:
            module_contents.append(content)

    # Build final output
    output_parts = [
        '#!/usr/bin/env python3',
        '"""',
        'Email Forwarder',
        'Forwards emails from specific senders to one or more recipients via IMAP/SMTP.',
        '',
        'This is a generated file - do not edit directly.',
        'Source: build/forwarder/',
        '"""',
        '',
    ]

    # Add sorted stdlib imports
    for imp in sorted(all_stdlib_imports):
        output_parts.append(imp)

    # Add typing imports
    if all_typing_imports:
        output_parts.append(f"from typing import {', '.join(sorted(all_typing_imports))}")

    output_parts.append('')
    output_parts.append('')

    # Add template constants
    output_parts.extend(template_constants)
    output_parts.append('')
    output_parts.append('')

    # Add module contents
    output_parts.append('\n\n\n'.join(module_contents))

    # Add main block
    output_parts.append('')
    output_parts.append('')
    output_parts.append("if __name__ == '__main__':")
    output_parts.append('    main()')
    output_parts.append('')

    # Post-process: replace template loading with inline constants
    output = '\n'.join(output_parts)

    # Replace _load_template calls with direct constant references
    output = re.sub(
        r"template = _load_template\('extracted_code'\)",
        "template = Template(TEMPLATE_CODE)",
        output
    )
    output = re.sub(
        r"template = _load_template\('extracted_link'\)",
        "template = Template(TEMPLATE_LINK)",
        output
    )

    # Remove the _load_template function and template cache
    output = re.sub(
        r"# Template cache\n_TEMPLATE_DIR = Path\(__file__\)\.parent / 'templates'\n_TEMPLATES: dict\[str, Template\] = \{\}\n\n\ndef _load_template\(name: str\) -> Template:\n    \"\"\"Load and cache HTML template\.\"\"\"\n    if name not in _TEMPLATES:\n        template_path = _TEMPLATE_DIR / f'\{name\}\.html'\n        _TEMPLATES\[name\] = Template\(template_path\.read_text\(\)\)\n    return _TEMPLATES\[name\]\n\n",
        "",
        output
    )

    # Write output
    OUTPUT_FILE.write_text(output)
    print(f'Built {OUTPUT_FILE} ({len(output)} bytes)')


if __name__ == '__main__':
    build()
