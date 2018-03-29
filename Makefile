ZIP_NAME=k8sasghelper.zip

.DEFAULT_GOAL: build

build:
	echo "Creating ZIP at ${ZIP_NAME} for deploy to Lambda"
	zip -u "${ZIP_NAME}" "__init__.py"
	zip -u "${ZIP_NAME}" "constants.py"
	zip -u "${ZIP_NAME}" "k8sasghelper.py"

	zip -u -r "${ZIP_NAME}" .

clean:
	echo "Cleaning up ${ZIP_NAME} if found"

	@if [ -e "${ZIP_NAME}" ]; \
		then \
		rm "${ZIP_NAME}"; \
	fi

.PHONY: clean
