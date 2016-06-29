.PHONY: lint-clcache lint-unittests lint-integrationtests lint

lint-clcache:
	pylint --rcfile .pylintrc clcache.py

lint-unittests:
	pylint --rcfile .pylintrc unittests.py

lint-integrationtests:
	pylint --rcfile .pylintrc integrationtests.py

lint: lint-clcache lint-unittests lint-integrationtests
