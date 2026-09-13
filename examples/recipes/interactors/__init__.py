"""Interactors with dishka: the container builds everything, including the controller.

Each interactor receives a gateway and the acting user from the dishka request scope;
``provide_controllers`` registers the controller and ``DishkaResolver`` checks at
startup that every dependency (constructor and ``Inject[...]``) is provided.
"""
