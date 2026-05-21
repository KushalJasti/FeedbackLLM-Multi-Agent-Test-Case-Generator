import sys
import os
import subprocess

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 main.py <path_to_program>")
        sys.exit(1)
        
    file_path = sys.argv[1]
    
    if not os.path.isfile(file_path):
        print(f"Error: File '{file_path}' not found.")
        sys.exit(1)
        
    _, ext = os.path.splitext(file_path)
    ext = ext.lower()
    
    if ext == ".py":
        print(f"Detected Python file. Routing to generator.py...")
        script_to_run = "generator.py"
    elif ext == ".c":
        print(f"Detected C file. Routing to generator_c.py...")
        script_to_run = "generator_c.py"
    else:
        print(f"Error: Unsupported file extension '{ext}'. Only .py and .c files are supported.")
        sys.exit(1)
        
    script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), script_to_run)
    
    if not os.path.exists(script_path):
        print(f"Error: '{script_to_run}' script not found in the same directory.")
        sys.exit(1)
        
    try:
        # Run the target generator script with the provided file path
        subprocess.run([sys.executable, script_path, file_path], check=True)
    except subprocess.CalledProcessError as e:
        print(f"\nExecution failed with error code {e.returncode}.")
        sys.exit(e.returncode)
    except KeyboardInterrupt:
        print("\nProcess interrupted by user.")
        sys.exit(1)

if __name__ == "__main__":
    main()
