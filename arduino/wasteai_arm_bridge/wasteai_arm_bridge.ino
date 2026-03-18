#include <Servo.h>

// Declaration des servos moteurs
Servo servoBase;
Servo servoElbow;
Servo servoWrist;
Servo servoGripper;

// Variables pour stocker les positions
int currentBase, currentElbow, currentWrist, currentGripper;

// Fonction pour deplacer les servos moteurs
void moveServos(int targetBase, int targetElbow, int targetWrist, int targetGripper, int stepDelay) {
  // Lire les positions actuelles
  int posBase = servoBase.read();
  int posElbow = servoElbow.read();
  int posWrist = servoWrist.read();
  int posGripper = servoGripper.read();

  // Boucle pour que les moteurs atteignent leurs cibles
  while (posBase != targetBase || posElbow != targetElbow || posWrist != targetWrist || posGripper != targetGripper) {
    if (posBase < targetBase) posBase++;
    else if (posBase > targetBase) posBase--;

    if (posElbow < targetElbow) posElbow++;
    else if (posElbow > targetElbow) posElbow--;

    if (posWrist < targetWrist) posWrist++;
    else if (posWrist > targetWrist) posWrist--;

    if (posGripper < targetGripper) posGripper++;
    else if (posGripper > targetGripper) posGripper--;

    servoBase.write(posBase);
    servoElbow.write(posElbow);
    servoWrist.write(posWrist);
    servoGripper.write(posGripper);
    delay(stepDelay);
  }
  
  // Mettre à jour les positions actuelles
  currentBase = targetBase;
  currentElbow = targetElbow;
  currentWrist = targetWrist;
  currentGripper = targetGripper;
}

// Fonction pour exécuter une séquence complète de ramassage
void pickupSequence() {
  Serial.println("Starting pickup sequence...");
  
  // etape 1 : Tourner le bras vers l'objet
  moveServos(150, 90, 90, 0, 15);
  
  // etape 2 : Abaisser l'epaule et plier le coude
  moveServos(150, 60, 100, 0, 20);
  
  // etape 3 : Fermer la pince pour saisir l'objet
  moveServos(150, 60, 100, 45, 25);
  
  // etape 4 : Relever le bras avec l'objet
  moveServos(150, 90, 90, 45, 25);
  
  // etape 5 : Tourner la base vers la zone de depot
  moveServos(30, 90, 90, 45, 15);
  
  // etape 6 : Abaisser le bras pour deposer 
  moveServos(30, 60, 100, 45, 30);
  
  // etape 7 : Ouvrir la pince pour relacher l'objet
  moveServos(30, 60, 100, 0, 25);
  
  // etape 8 : Relever le bras et revenir a la position initiale 
  moveServos(90, 90, 90, 0, 20);
  
  Serial.println("Pickup sequence completed");
}

// Fonction pour retourner à la position de repos
void homePosition() {
  Serial.println("Returning to home position...");
  moveServos(90, 90, 90, 0, 20);
  Serial.println("Home position reached");
}

// Fonction pour ouvrir la pince
void openGripper() {
  moveServos(currentBase, currentElbow, currentWrist, 0, 10);
  Serial.println("Gripper opened");
}

// Fonction pour fermer la pince
void closeGripper() {
  moveServos(currentBase, currentElbow, currentWrist, 45, 10);
  Serial.println("Gripper closed");
}

void setup() {
  // Initialiser la communication série
  Serial.begin(9600);
  while (!Serial) { ; } // Attendre que le port série soit prêt
  
  // Attacher les servos moteurs aux pins 
  servoBase.attach(2);
  servoElbow.attach(3);
  servoWrist.attach(4);
  servoGripper.attach(5);
  
  // Fixer des positions initiales
  servoBase.write(90);
  servoElbow.write(90);
  servoWrist.write(90);
  servoGripper.write(0);
  
  currentBase = 90;
  currentElbow = 90;
  currentWrist = 90;
  currentGripper = 0;
  
  delay(1000);
  
  Serial.println("Arm controller ready");
  Serial.println("Commands: PICKUP, HOME, OPEN, CLOSE, STOP");
}

void loop() {
  // Vérifier si des commandes sont disponibles sur le port série
  if (Serial.available() > 0) {
    String command = Serial.readStringUntil('\n');
    command.trim();  // Enlever les espaces et retours à la ligne
    
    if (command.length() == 0) return;
    
    // Traiter les commandes
    if (command == "PICKUP") {
      pickupSequence();
      Serial.println("OK");
    }
    else if (command == "HOME") {
      homePosition();
      Serial.println("OK");
    }
    else if (command == "OPEN") {
      openGripper();
      Serial.println("OK");
    }
    else if (command == "CLOSE") {
      closeGripper();
      Serial.println("OK");
    }
    else if (command == "STOP") {
      // Arrêt d'urgence - on arrête tout mouvement
      Serial.println("Emergency stop");
      // On ne fait rien de spécial ici car les servos maintiennent leur position
      Serial.println("OK");
    }
    else if (command.startsWith("MOVE ")) {
      // Format: MOVE base elbow wrist gripper delay
      // Exemple: MOVE 150 60 100 45 20
      int values[5];
      int index = 0;
      String temp = command.substring(5); // Enlever "MOVE "
      
      char *ptr = strtok((char*)temp.c_str(), " ");
      while (ptr != NULL && index < 5) {
        values[index++] = atoi(ptr);
        ptr = strtok(NULL, " ");
      }
      
      if (index == 5) {
        moveServos(values[0], values[1], values[2], values[3], values[4]);
        Serial.println("OK");
      } else {
        Serial.println("ERROR: Invalid MOVE command format");
      }
    }
    else {
      Serial.println("ERROR: Unknown command");
    }
  }
}