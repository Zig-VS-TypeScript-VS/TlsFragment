cd ..
pip install -r requirements.txt
pyinstaller -F run.py -n proxy.exe --noconsole
copy /Y config.json dist\config.json
pause