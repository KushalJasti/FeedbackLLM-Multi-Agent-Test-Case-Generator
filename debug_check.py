import os

output_dir = "/Users/kushaljasti/Documents/capstone/ksllm2/TestCases/bank_system_testcases"
testcase_path = os.path.join(output_dir, "testcase1.txt")

print(f"Output Directory: {output_dir}")
print(f"Exists: {os.path.exists(output_dir)}")
print(f"Is Directory: {os.path.isdir(output_dir)}")
print(f"Permissions: {oct(os.stat(output_dir).st_mode)[-3:]}")

print(f"Testcase Path: {testcase_path}")
if os.path.exists(testcase_path):
    print(f"File Exists. Permissions: {oct(os.stat(testcase_path).st_mode)[-3:]}")
else:
    print("File does not exist.")

try:
    print("Attempting to write to file...")
    with open(testcase_path, "w") as f:
        f.write("test")
    print("Write successful.")
except Exception as e:
    print(f"Write failed: {e}")
