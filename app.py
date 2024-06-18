from flask import Flask, render_template, request, send_file, jsonify
import os
import xml.etree.ElementTree as ET
from zipfile import ZipFile
import cairosvg
from celery import Celery
from celery_worker import make_celery

app = Flask(__name__)
app.config.update(
    CELERY_BROKER_URL='redis://localhost:6379/0',
    CELERY_RESULT_BACKEND='redis://localhost:6379/0'
)
celery = make_celery(app)

UPLOAD_FOLDER = 'static/uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def convert_svg_to_png(svg_file, output_png):
    cairosvg.svg2png(url=svg_file, write_to=output_png)
    return output_png

def separate_svg_layers(svg_file):
    tree = ET.parse(svg_file)
    root = tree.getroot()
    
    layers = {}
    
    for elem in root.iter():
        if 'id' in elem.attrib:
            elem_id = elem.attrib['id']
            if elem_id not in layers:
                layers[elem_id] = ET.Element('svg', attrib={'xmlns': 'http://www.w3.org/2000/svg'})
            layers[elem_id].append(elem)
    
    layer_paths = []
    for layer_id, layer_root in layers.items():
        layer_path = os.path.join(UPLOAD_FOLDER, f'{layer_id}.svg')
        layer_tree = ET.ElementTree(layer_root)
        layer_tree.write(layer_path, encoding='utf-8', xml_declaration=True)
        layer_paths.append((layer_id, layer_path))
    
    return layer_paths

def convert_svg_to_gcode(filepath, color):
    tree = ET.parse(filepath)
    root = tree.getroot()
    namespaces = {'svg': 'http://www.w3.org/2000/svg'}
    gcode_lines = []

    gcode_lines.append(f"; Color: {color}\n")
    for path in root.findall('.//svg:path', namespaces):
        d = path.attrib.get('d')
        if d:
            gcode_lines.extend(path_to_gcode(d, color))
    gcode_lines.append("\n")
    return gcode_lines

def path_to_gcode(path_data, color):
    gcode_lines = []
    commands = path_data.split()
    gcode_lines.append(f"; Start of path for color {color}\n")
    for command in commands:
        if command.startswith('M'):
            gcode_lines.append(f"G0 {command[1:]}\n")
        elif command.startswith('L'):
            x, y = command[1:].split(',')
            gcode_lines.append(f"; Move to X{x} Y{y}\n")
            gcode_lines.append(f"G1 X{x} Y{y}\n")
    gcode_lines.append(f"; End of path for color {color}\n")
    return gcode_lines

@celery.task
def process_svg_to_gcode(file_path, file_name):
    filepath = file_path
    file_name = file_name
    
    # Convert original SVG to PNG
    png_filepath = os.path.join(UPLOAD_FOLDER, f'{os.path.splitext(file_name)[0]}.png')
    convert_svg_to_png(filepath, png_filepath)
    
    layers = separate_svg_layers(filepath)
    
    gcode_filepaths = []
    for color, layer_path in layers:
        gcode_lines = convert_svg_to_gcode(layer_path, color)
        gcode_filepath = os.path.join(UPLOAD_FOLDER, f'{color}.gcode')
        with open(gcode_filepath, 'w') as gcode_file:
            gcode_file.writelines(gcode_lines)
        gcode_filepaths.append(gcode_filepath)

    zip_filepath = os.path.join(UPLOAD_FOLDER, 'files.zip')
    with ZipFile(zip_filepath, 'w') as zipf:
        zipf.write(filepath, os.path.basename(filepath))  # Include original SVG file
        zipf.write(png_filepath, os.path.basename(png_filepath))  # Include PNG file
        for gcode_filepath in gcode_filepaths:
            zipf.write(gcode_filepath, os.path.basename(gcode_filepath))
    
    return zip_filepath

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        if 'svg_file' not in request.files:
            return jsonify({'success': False, 'error': 'No file part'}), 400
        file = request.files['svg_file']
        if file.filename == '':
            return jsonify({'success': False, 'error': 'No selected file'}), 400
        file_path = os.path.join(UPLOAD_FOLDER, file.filename)
        file.save(file_path)
        
        task = process_svg_to_gcode.apply_async(args=[file_path, file.filename])
        return jsonify({
            'success': True,
            'task_id': task.id
        })
    return render_template('index.html')

@app.route('/status/<task_id>')
def task_status(task_id):
    task = process_svg_to_gcode.AsyncResult(task_id)
    if task.state == 'PENDING':
        response = {
            'state': task.state,
            'status': 'Pending...'
        }
    elif task.state != 'FAILURE':
        response = {
            'state': task.state,
            'status': task.info.get('status', '')
        }
        if 'result' in task.info:
            response['result'] = task.info['result']
    else:
        response = {
            'state': task.state,
            'status': str(task.info)
        }
    return jsonify(response)

@app.route('/download/<filename>')
def download_file(filename):
    return send_file(os.path.join(UPLOAD_FOLDER, filename), as_attachment=True)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)