# CadastreAutomation distribution package

Share this folder as a ZIP file with other Windows users.

## Install

1. Unzip the ZIP file.
2. Double-click `Install.cmd`.
3. Choose the installation folder when the folder selection window appears.
4. Run the Cadastre shortcut created on the Desktop.

If you cancel the folder selection window, installation stops.

## Requirements

- QGIS 3.x
- Chrome or Edge
- VWorld API key
- VWorld account if the download page requires login

## Installed files

The installer copies the program into the folder selected by the user. The selected folder will contain:

```text
app
styles
```

Output files are saved to the user's Desktop cadastre output folder.

If installation fails, check `install.log` in this package folder.

## Notes before sharing

- `styles\cadastre-default.qml` is the default cadastre style.
- To use a different style, replace that file before sharing the ZIP.
- VWorld API keys and VWorld passwords are not saved in the package. Users enter them when running the program.
