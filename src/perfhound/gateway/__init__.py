"""Gateway: the single entry point to git / GitHub data.

Every other Perfhound module asks the gateway for commit data and never
calls git or GitHub directly (Facade + Adapter patterns).
"""
